import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiBaseUrl,
  apiPath,
  detailOf,
  makeClient,
  NetworkError,
  onUnauthorized,
  onUnreachable,
  unwrap,
} from "./client";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("apiBaseUrl", () => {
  it.each([
    ["http://nas:8080/", "http://nas:8080"],
    ["http://my.domain/audible-sync/", "http://my.domain/audible-sync"],
    ["https://my.domain/apps/audible-sync/", "https://my.domain/apps/audible-sync"],
    // No <base>: the page's own address, whose last segment is a file or a route
    ["http://nas:8080/library", "http://nas:8080"],
    ["http://nas:8080/audible-sync/?q=1#x", "http://nas:8080/audible-sync"],
  ])("%s gives %s", (base, expected) => {
    expect(apiBaseUrl(base)).toBe(expected);
  });
});

describe("apiPath", () => {
  it("drops the client's base path in front of /api", () => {
    expect(apiPath("http://my.domain/audible-sync/api/books/12?x=1", "http://my.domain/audible-sync")).toBe(
      "/api/books/12",
    );
    expect(apiPath("http://nas:8080/api/status", "http://nas:8080")).toBe("/api/status");
  });

  it("drops the whole base even when it has an api segment of its own", () => {
    expect(apiPath("http://my.domain/x/api/sync/api/books/12", "http://my.domain/x/api/sync")).toBe("/api/books/12");
  });
});

describe("detailOf", () => {
  it("reads the API's own detail", () => {
    expect(detailOf({ detail: "No book with id 3" }, "fallback")).toBe("No book with id 3");
  });

  it("joins FastAPI's field errors, naming each field", () => {
    const body = {
      detail: [
        { loc: ["body", "bitrate"], msg: "Input should be less than 257" },
        { loc: ["query", "page"], msg: "Input should be greater than 0" },
      ],
    };
    expect(detailOf(body, "fallback")).toBe(
      "bitrate: Input should be less than 257; query.page: Input should be greater than 0",
    );
  });

  it("falls back for anything else", () => {
    expect(detailOf("<html>Bad Gateway</html>", "Bad Gateway")).toBe("Bad Gateway");
    expect(detailOf({ detail: [] }, "fallback")).toBe("fallback");
    expect(detailOf(null, "fallback")).toBe("fallback");
  });
});

describe("unwrap", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the data of a successful call", async () => {
    const client = makeClient("http://nas:8080", async () => json(200, { status: "ok", version: "2.0.0" }));

    await expect(unwrap(client.GET("/api/health"))).resolves.toEqual({ status: "ok", version: "2.0.0" });
  });

  it("throws an ApiError naming the request, with the detail to show", async () => {
    const client = makeClient("http://my.domain/audible-sync", async () => json(404, { detail: "No book with id 12" }));

    const error = await unwrap(client.GET("/api/books/{book_id}", { params: { path: { book_id: 12 } } })).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: "No book with id 12", method: "GET", path: "/api/books/12" });
  });

  it("throws a NetworkError when nothing answers", async () => {
    const client = makeClient("http://nas:8080", async () => {
      throw new TypeError("Failed to fetch");
    });

    await expect(unwrap(client.GET("/api/status"))).rejects.toBeInstanceOf(NetworkError);
  });

  it("calls the unreachable listeners when nothing answers", async () => {
    const listener = vi.fn();
    const stop = onUnreachable(listener);
    const client = makeClient("http://nas:8080", async () => {
      throw new TypeError("Failed to fetch");
    });

    await unwrap(client.GET("/api/status")).catch(() => undefined);
    stop();

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("passes a cancelled request's AbortError on without calling the service unreachable", async () => {
    const listener = vi.fn();
    const stop = onUnreachable(listener);
    const controller = new AbortController();
    const client = makeClient("http://nas:8080", (input) => {
      const request = input as Request; // openapi-fetch always hands fetch a Request
      const aborted = new Promise<Response>((_, reject) => {
        request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
      controller.abort();
      return aborted;
    });

    const error = await unwrap(client.GET("/api/status", { signal: controller.signal })).catch((e: unknown) => e);
    stop();

    expect(error).not.toBeInstanceOf(NetworkError);
    expect(error).toMatchObject({ name: "AbortError" });
    expect(listener).not.toHaveBeenCalled();
  });

  it("calls the unauthorized listeners on a 401, but not for a failed sign-in", async () => {
    const listener = vi.fn();
    const stop = onUnauthorized(listener);
    const client = makeClient("http://nas:8080", async () => json(401, { detail: "Not authenticated" }));

    await unwrap(client.GET("/api/status")).catch(() => undefined);
    await unwrap(
      client.POST("/api/auth/login", { body: { username: "admin", password: "wrong", remember: false } }),
    ).catch(() => undefined);
    stop();

    expect(listener).toHaveBeenCalledTimes(1);
  });
});
