/**
 * The API client: openapi-fetch over the types generated from the service's schema
 * (`npm run gen:api`), so every path, query and body is checked against src/schemas.py.
 *
 * Requests go to the address the page was served under: the service writes
 * <base href="{base path}/"> into index.html, so the same build works at the root of
 * a domain and behind a reverse proxy at /audible-sync/.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

/** The service's address: the page's base URL without its trailing slash. */
export function apiBaseUrl(baseURI: string = document.baseURI): string {
  const url = new URL(baseURI);
  // A base names a folder; anything after its last slash is a file name
  const folder = url.pathname.replace(/[^/]*$/, "");
  return `${url.origin}${folder.replace(/\/$/, "")}`;
}

/** The API answered with an error status; `detail` is its message, ready to show. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly method: string,
    readonly path: string,
    readonly headers: Headers = new Headers(),
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

/** No answer at all: the service is down or unreachable, not an error status. */
export class NetworkError extends Error {
  constructor(
    readonly method: string,
    readonly path: string,
    cause: unknown,
  ) {
    super(`No response from the service (${method} ${path})`, { cause });
    this.name = "NetworkError";
  }
}

/**
 * The text to show for an error body. The API's errors are `{"detail": "..."}`; a
 * request FastAPI itself rejects has a list of field errors as its detail instead.
 */
export function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => {
          if (!item || typeof item !== "object") return null;
          const { loc, msg } = item as { loc?: unknown; msg?: unknown };
          if (typeof msg !== "string") return null;
          const field = Array.isArray(loc) ? loc.filter((part) => part !== "body").join(".") : "";
          return field ? `${field}: ${msg}` : msg;
        })
        .filter((message): message is string => message !== null);
      if (messages.length) {
        return messages.join("; ");
      }
    }
  }
  return fallback;
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
const unreachableListeners = new Set<Listener>();

/** Called on any 401 except a failed sign-in: the session has ended. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

/** Called when a request gets no response at all. */
export function onUnreachable(listener: Listener): () => void {
  unreachableListeners.add(listener);
  return () => unreachableListeners.delete(listener);
}

// Wrong credentials there are the form's own error, not an ended session
const SIGN_IN = "/api/auth/login";

/** The API path of a URL, without the base path in front: `/api/books/12`. */
export function apiPath(url: string): string {
  const { pathname } = new URL(url, "http://localhost/");
  const at = pathname.indexOf("/api/");
  return at >= 0 ? pathname.slice(at) : pathname;
}

// What each response answered, so an error can name its request ("GET /api/books/12")
const requests = new WeakMap<Response, { method: string; path: string }>();

const events: Middleware = {
  onResponse({ request, response, schemaPath }) {
    requests.set(response, { method: request.method, path: apiPath(request.url) });
    if (response.status === 401 && schemaPath !== SIGN_IN) {
      for (const listener of unauthorizedListeners) listener();
    }
    return undefined;
  },
  onError({ error, request }) {
    for (const listener of unreachableListeners) listener();
    return new NetworkError(request.method, apiPath(request.url), error);
  },
};

export function makeClient(baseUrl: string, fetchImpl?: typeof fetch) {
  const client = createClient<paths>({
    baseUrl,
    credentials: "same-origin",
    // Looked up per request rather than captured now, so a test can stub the global
    fetch: fetchImpl ?? ((request: Request) => globalThis.fetch(request)),
  });
  client.use(events);
  return client;
}

export const api = makeClient(apiBaseUrl());

type Result<T> = { data: T; error?: never; response: Response } | { data?: never; error: unknown; response: Response };

/**
 * The data of a call, or an `ApiError` carrying the API's `detail`: what a query or a
 * mutation function returns, so errors reach TanStack Query as rejections.
 */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  const result = await call;
  const { response } = result;
  if (result.error !== undefined || !response.ok) {
    const request = requests.get(response) ?? { method: "", path: apiPath(response.url) };
    throw new ApiError(
      response.status,
      detailOf(result.error, response.statusText || `HTTP ${response.status}`),
      request.method,
      request.path,
      response.headers,
    );
  }
  return result.data as T;
}
