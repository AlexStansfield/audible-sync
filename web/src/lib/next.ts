/**
 * Where to go after signing in: the `next` parameter when it is a path inside this
 * app, else the dashboard. Anything else - another origin, `//host`, a backslash
 * trick - is ignored, so a crafted sign-in link cannot send you off-site.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next?.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return "/";
  }
  if (next === "/login" || next.startsWith("/login?") || next.startsWith("/login/")) {
    return "/";
  }
  return next;
}

/** The sign-in page's address, returning to `path` (the page you were on) afterwards. */
export function loginPath(path: string): string {
  const next = safeNext(path);
  return next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
}
