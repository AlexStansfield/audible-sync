/**
 * A fake service behind `fetch`, for tests: answers by "METHOD /path" (the query
 * string ignored), records every request, and is a 404 for anything not set up.
 */
import { vi } from "vitest";
import type { Me, Status } from "../api/queries";

export type Reply =
  | Response
  | (() => Response | Promise<Response>)
  | ((request: Request) => Response | Promise<Response>);

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export const ok = (body: unknown) => () => json(200, body);
export const fail =
  (status: number, detail: string, headers: Record<string, string> = {}) =>
  () =>
    json(status, { detail }, headers);
/** No answer at all: what fetch does when the service is down. */
export const unreachable = () => () => Promise.reject(new TypeError("Failed to fetch"));

export type FakeApi = {
  routes: Record<string, Reply>;
  calls: { method: string; path: string; url: string; body: string }[];
  /** The bodies sent to a route, parsed */
  sent: (route: string) => unknown[];
};

export function fakeApi(routes: Record<string, Reply>): FakeApi {
  const api: FakeApi = {
    routes,
    calls: [],
    sent: (route) =>
      api.calls
        .filter((call) => `${call.method} ${call.path}` === route)
        .map((call) => JSON.parse(call.body || "null")),
  };
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);
    const body = request.method === "GET" || request.method === "HEAD" ? "" : await request.clone().text();
    api.calls.push({ method: request.method, path: url.pathname, url: request.url, body });
    const reply = api.routes[`${request.method} ${url.pathname}`];
    if (!reply) return json(404, { detail: "Not Found" });
    if (reply instanceof Response) return reply.clone();
    return reply(request);
  });
  return api;
}

export const ME: Me = { username: "admin", generated_password: false };

export function status(overrides: Partial<Status> = {}): Status {
  return {
    scheduler: { enabled: true, interval_minutes: 360, next_run_at: "2026-11-02T18:00:00+00:00", running: false },
    current_run: null,
    last_run: null,
    accounts: [],
    ...overrides,
  };
}

/** The routes every signed-in screen calls: who you are, the version, the status, the count. */
export function signedIn(extra: Record<string, Reply> = {}): Record<string, Reply> {
  return {
    "GET /api/auth/me": ok(ME),
    "GET /api/health": ok({ status: "ok", version: "2.0.0" }),
    "GET /api/status": ok(status()),
    "GET /api/books": ok({ items: [], total: 354, page: 1, page_size: 1 }),
    ...extra,
  };
}
