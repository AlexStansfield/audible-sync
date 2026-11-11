/**
 * The service worker as the browser runs it. The template has the build written into
 * it as the build step does, and it runs in a sandbox whose `self`, `caches` and
 * `fetch` are fakes, driven by dispatching its events.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { THEME_COLOR } from "../lib/theme";
import { buildVersion, injectBuild, precacheList } from "./serviceWorker";

const WEB = join(__dirname, "..", "..");
const TEMPLATE = readFileSync(join(WEB, "src", "pwa", "sw.js"), "utf-8");
const ORIGIN = "https://books.example";

type FakeRequest = { url: string; method: string; mode: string; cache?: string };

class FakeRequestClass {
  url: string;
  method = "GET";
  mode = "cors";
  cache: string | undefined;
  constructor(url: string, init?: { cache?: string }, base = "") {
    this.url = new URL(url, base).href;
    this.cache = init?.cache;
  }
}

function loadWorker({
  scope = `${ORIGIN}/`,
  precache = ["./", "assets/app-1a2b.js", "manifest.webmanifest"],
  version = "v2",
  caches: existing = [] as string[],
} = {}) {
  const stores = new Map<string, Map<string, Response>>(existing.map((name) => [name, new Map()]));
  const fetched: FakeRequest[] = [];
  let network: (request: FakeRequest) => Promise<Response> = async (request) =>
    new Response(`network ${new URL(request.url).pathname}`);
  const fetchImpl = vi.fn(async (request: FakeRequest) => {
    fetched.push(request);
    return network(request);
  });
  const store = (name: string) => {
    let entries = stores.get(name);
    if (!entries) {
      entries = new Map();
      stores.set(name, entries);
    }
    return entries;
  };
  const key = (request: FakeRequest | string) => (typeof request === "string" ? request : request.url);
  const cacheApi = {
    open: async (name: string) => {
      const entries = store(name);
      return {
        match: async (request: FakeRequest | string) => entries.get(key(request))?.clone(),
        put: async (request: FakeRequest, response: Response) => {
          entries.set(key(request), response);
        },
        addAll: async (requests: FakeRequest[]) => {
          for (const request of requests) {
            const response = await fetchImpl(request);
            if (!response.ok) throw new TypeError("addAll: a response was not ok");
            entries.set(request.url, response);
          }
        },
      };
    },
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
  };

  const listeners = new Map<string, (event: unknown) => void>();
  const self = {
    location: new URL("sw.js", scope),
    registration: { scope },
    addEventListener: (type: string, listener: (event: unknown) => void) => listeners.set(type, listener),
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn(async () => undefined) },
  };
  // A Request resolves a relative URL against the worker's own address, as in a browser
  const RequestClass = class extends FakeRequestClass {
    constructor(url: string, init?: { cache?: string }) {
      super(url, init, self.location.href);
    }
  };
  runInNewContext(injectBuild(TEMPLATE, version, precache), {
    self,
    caches: cacheApi,
    fetch: fetchImpl,
    Request: RequestClass,
    URL,
    Response,
  });

  const lifecycle = async (type: "install" | "activate") => {
    const waits: Promise<unknown>[] = [];
    listeners.get(type)?.({ waitUntil: (promise: Promise<unknown>) => waits.push(promise) });
    await Promise.all(waits);
  };

  /** The worker's answer, or undefined when it leaves the request to the browser. */
  const request = async (path: string, { mode = "cors", method = "GET" } = {}) => {
    let answer: Promise<Response> | undefined;
    listeners.get("fetch")?.({
      request: { url: new URL(path, scope).href, method, mode },
      respondWith: (response: Promise<Response>) => {
        answer = response;
      },
    });
    return answer === undefined ? undefined : await answer;
  };

  return {
    self,
    stores,
    fetched,
    lifecycle,
    request,
    message: (data: unknown) => listeners.get("message")?.({ data }),
    setNetwork: (next: typeof network) => {
      network = next;
    },
  };
}

const offline = async (): Promise<Response> => {
  throw new TypeError("Failed to fetch");
};

describe("installing", () => {
  it("caches the shell under the scope, fetched past the HTTP cache", async () => {
    const worker = loadWorker({ scope: `${ORIGIN}/audible-sync/` });

    await worker.lifecycle("install");

    expect(worker.fetched.map((request) => [request.url, request.cache])).toEqual([
      [`${ORIGIN}/audible-sync/`, "reload"],
      [`${ORIGIN}/audible-sync/assets/app-1a2b.js`, "reload"],
      [`${ORIGIN}/audible-sync/manifest.webmanifest`, "reload"],
    ]);
    expect([...worker.stores.keys()]).toEqual(["audible-sync-v2"]);
  });

  it("drops the caches of older builds when it takes over, and nothing else's", async () => {
    const worker = loadWorker({ caches: ["audible-sync-v1", "someone-else"] });
    await worker.lifecycle("install");

    await worker.lifecycle("activate");

    expect([...worker.stores.keys()].sort()).toEqual(["audible-sync-v2", "someone-else"]);
    expect(worker.self.clients.claim).toHaveBeenCalled();
  });

  it("waits until the page asks it to take over", async () => {
    const worker = loadWorker();
    await worker.lifecycle("install");
    expect(worker.self.skipWaiting).not.toHaveBeenCalled();

    worker.message({ type: "something else" });
    worker.message(null);
    expect(worker.self.skipWaiting).not.toHaveBeenCalled();

    worker.message({ type: "SKIP_WAITING" });
    expect(worker.self.skipWaiting).toHaveBeenCalledOnce();
  });
});

describe("answering", () => {
  it.each([
    "/audible-sync/",
    "/audible-sync/library?status=failed",
    "/audible-sync/books/12",
    "/audible-sync/index.html",
  ])("opens the cached shell for %s with the service down", async (path) => {
    const worker = loadWorker({ scope: `${ORIGIN}/audible-sync/` });
    await worker.lifecycle("install");
    worker.setNetwork(offline);

    const response = await worker.request(path, { mode: "navigate" });

    expect(await response?.text()).toBe("network /audible-sync/");
  });

  it.each([
    ["/api/status", "cors"],
    ["/api/books/12/cover", "no-cors"],
    ["/api", "navigate"],
    ["/api/health", "navigate"],
    ["/docs", "navigate"],
    ["/redoc", "navigate"],
    ["/openapi.json", "cors"],
  ])("leaves %s (%s) to the network, never a cache", async (path, mode) => {
    const worker = loadWorker();
    await worker.lifecycle("install");

    expect(await worker.request(path, { mode })).toBeUndefined();
  });

  it("leaves anything but a GET, and anything outside its scope, to the network", async () => {
    const worker = loadWorker({ scope: `${ORIGIN}/audible-sync/` });
    await worker.lifecycle("install");

    expect(await worker.request("/audible-sync/assets/app-1a2b.js", { method: "POST" })).toBeUndefined();
    expect(await worker.request("/other-app/", { mode: "navigate" })).toBeUndefined();
    expect(await worker.request("/audible-sync", { mode: "navigate" })).toBeUndefined();
    expect(await worker.request("https://elsewhere.example/audible-sync/", { mode: "navigate" })).toBeUndefined();
  });

  it("serves the shell's files from the cache", async () => {
    const worker = loadWorker();
    await worker.lifecycle("install");
    worker.setNetwork(offline);

    const response = await worker.request("/assets/app-1a2b.js");

    expect(await response?.text()).toBe("network /assets/app-1a2b.js");
  });

  it("keeps a file under assets/ the first time it is used, and nothing that failed", async () => {
    const worker = loadWorker();
    await worker.lifecycle("install");
    worker.setNetwork(async (request) =>
      request.url.endsWith("missing.woff2") ? new Response("", { status: 404 }) : new Response("font"),
    );

    expect(await (await worker.request("/assets/inter-latin-400.woff2"))?.text()).toBe("font");
    expect((await worker.request("/assets/missing.woff2"))?.status).toBe(404);
    worker.setNetwork(offline);

    expect(await (await worker.request("/assets/inter-latin-400.woff2"))?.text()).toBe("font");
    await expect(worker.request("/assets/missing.woff2")).rejects.toThrow("Failed to fetch");
  });

  it("does not keep a file outside assets/ that is not part of the shell", async () => {
    const worker = loadWorker();
    await worker.lifecycle("install");
    await worker.request("/robots.txt");
    worker.setNetwork(offline);

    await expect(worker.request("/robots.txt")).rejects.toThrow("Failed to fetch");
  });
});

describe("the build step", () => {
  it("lists the shell: the page as ./, scripts, styles, manifest, favicon and icons", () => {
    const files = [
      "index.html",
      "assets/index-B9.js",
      "assets/index-D_.css",
      "assets/inter-latin-400-normal.woff2",
      "assets/index-B9.js.map",
      "favicon.svg",
      "manifest.webmanifest",
      "icons/icon-192.png",
      "icons/apple-touch-icon.png",
    ];

    expect(precacheList(files)).toEqual([
      "./",
      "assets/index-B9.js",
      "assets/index-D_.css",
      "favicon.svg",
      "icons/apple-touch-icon.png",
      "icons/icon-192.png",
      "manifest.webmanifest",
    ]);
  });

  it("writes the build into the marked line, and refuses a template without it", () => {
    const worker = injectBuild(TEMPLATE, "abc", ["./"]);

    expect(worker).toContain('const BUILD = {"version":"abc","precache":["./"]};');
    expect(worker).not.toContain("build:inject");
    expect(() => injectBuild("const BUILD = {};", "abc", ["./"])).toThrow("build:inject");
  });

  it("gives the same build the same version, and any change another", () => {
    const page = { path: "index.html", content: new TextEncoder().encode("<html>") };
    const build = [page, { path: "assets/a.js", content: new TextEncoder().encode("a") }];
    const version = buildVersion(build);

    expect(buildVersion([...build].reverse())).toBe(version);
    expect(buildVersion([page, { path: "assets/a.js", content: new TextEncoder().encode("b") }])).not.toBe(version);
    expect(buildVersion([page])).not.toBe(version);
  });
});

describe("what the browser reads to install it", () => {
  const manifest = JSON.parse(readFileSync(join(WEB, "public", "manifest.webmanifest"), "utf-8"));
  const index = readFileSync(join(WEB, "index.html"), "utf-8");

  it("installs at whatever path the service is under", () => {
    expect(manifest.start_url).toBe("./");
    expect(manifest.scope).toBe("./");
    expect(manifest.display).toBe("standalone");
    for (const icon of manifest.icons) {
      expect(icon.src).not.toMatch(/^\//);
    }
    expect(index).toContain('href="manifest.webmanifest"');
    expect(index).toContain('href="icons/apple-touch-icon.png"');
  });

  it("colours the title bar as lib/theme.ts does", () => {
    expect(manifest.theme_color).toBe(THEME_COLOR.dark);
    expect(index).toContain(`dark ? "${THEME_COLOR.dark}" : "${THEME_COLOR.light}"`);
  });

  it("has every icon it names, a maskable one among them", () => {
    for (const icon of manifest.icons) {
      expect(() => readFileSync(join(WEB, "public", icon.src))).not.toThrow();
    }
    expect(manifest.icons.map((icon: { purpose: string }) => icon.purpose)).toContain("maskable");
    expect(manifest.icons.map((icon: { sizes: string }) => icon.sizes)).toEqual(
      expect.arrayContaining(["192x192", "512x512"]),
    );
  });
});
