/**
 * The web app's service worker, which makes it installable and lets the app shell open
 * with the service down, where it shows Cannot reach Audible Sync.
 *
 * - It caches the shell and never anything under `api/`. The shell is `index.html`, the
 *   build's scripts and styles, the manifest and the icons. Other files under `assets/`,
 *   such as the fonts, are cached the first time they are used.
 * - Every page within its scope opens the cached `index.html`, and the browser's router
 *   takes it from there. The pages the service makes itself (`docs`, `redoc`,
 *   `openapi.json`) go to the network.
 * - A new build installs beside the running one and waits. The page offers it as
 *   "Update available", and only when someone takes it up does the page post
 *   `SKIP_WAITING`. An open page is never swapped under itself. With every window
 *   closed, the next one opens the new build.
 *
 * Plain JavaScript, served as it is. `src/pwa/serviceWorker.ts` writes the build's
 * version and file list into the line marked below and puts it at `dist/sw.js`, at the
 * top of the app, so its scope is the whole app at the root or under a base path.
 */

const BUILD = { version: "dev", precache: ["./"] }; // build:inject

const CACHE = `audible-sync-${BUILD.version}`;
const SHELL = "./";
// The service's own pages, which are not the web app's
const SERVER_PAGES = new Set(["docs", "redoc", "openapi.json"]);

/** The path within the scope, without the leading slash, or null when outside it. */
function scoped(url) {
  const scope = new URL(self.registration.scope);
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return null;
  return url.pathname.slice(scope.pathname.length);
}

/** What to do with a request: "network", "shell" or "cache". */
function route(request) {
  if (request.method !== "GET") return "network";
  const path = scoped(new URL(request.url));
  if (path === null) return "network";
  const first = path.split("/", 1)[0];
  // Never the API, whose answers are the service's state now, nor the service's own pages
  if (first === "api" || SERVER_PAGES.has(first)) return "network";
  if (request.mode === "navigate") return "shell";
  return "cache";
}

self.addEventListener("install", (event) => {
  // The shell is fetched afresh, never from the HTTP cache, so it is this build's
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(BUILD.precache.map((url) => new Request(url, { cache: "reload" })))),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((name) => name.startsWith("audible-sync-") && name !== CACHE).map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const how = route(event.request);
  if (how === "network") return;
  if (how === "shell") {
    event.respondWith(fromCache(new URL(SHELL, self.registration.scope).href, event.request));
    return;
  }
  event.respondWith(fromCache(event.request, event.request));
});

/**
 * The cached answer for `key`, or the network's for `request`. A file under `assets/`
 * got from the network is kept: its name changes with its content.
 */
async function fromCache(key, request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(key);
  if (hit) return hit;
  const response = await fetch(request);
  const path = scoped(new URL(request.url));
  if (response.ok && path?.startsWith("assets/")) {
    await cache.put(request, response.clone());
  }
  return response;
}
