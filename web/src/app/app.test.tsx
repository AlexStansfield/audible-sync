import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient } from "../api/queryClient";
import { RESET_COMMAND } from "../auth/LoginPage";
import { fail, fakeApi, ME, ok, signedIn, status, unreachable } from "../test/fakeApi";
import { renderApp } from "../test/render";
import { basenameFrom } from "./routes";
import { retryDelay, useReachability } from "./useReachability";

/**
 * Waits until the sign-in page has asked whether this browser is still signed in, so a
 * test can then switch the fake to "signed in" without that check seeing it first.
 */
async function signInChecked(api: ReturnType<typeof fakeApi>, times: number) {
  await waitFor(() => expect(api.calls.filter((call) => call.path === "/api/auth/me").length).toBe(times));
}

const where = (router: ReturnType<typeof renderApp>["router"]) =>
  router.state.location.pathname + router.state.location.search;

describe("signing in", () => {
  it("sends a signed-out visit to the sign-in, then back to the page asked for", async () => {
    const api = fakeApi({ "GET /api/auth/me": fail(401, "Not authenticated") });
    const { router } = renderApp("/library?status=failed");

    await screen.findByRole("heading", { name: "Sign in to Audible Sync" });
    expect(where(router)).toBe("/login?next=%2Flibrary%3Fstatus%3Dfailed");
    // The guard's check, then the sign-in page's own
    await signInChecked(api, 2);

    Object.assign(api.routes, signedIn(), { "POST /api/auth/login": ok(ME) });
    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(where(router)).toBe("/library?status=failed"));
    expect(api.sent("POST /api/auth/login")).toEqual([
      { username: "admin", password: "a long password", remember: true },
    ]);
  });

  it("says a wrong username or password without saying which", async () => {
    fakeApi({
      "GET /api/auth/me": fail(401, "Not authenticated"),
      "POST /api/auth/login": fail(401, "Wrong username or password"),
    });
    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
  });

  it("says how long to wait after too many attempts", async () => {
    fakeApi({
      "GET /api/auth/me": fail(401, "Not authenticated"),
      "POST /api/auth/login": fail(429, "Too many failed attempts; try again later", { "Retry-After": "240" }),
    });
    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed attempts. Try again in 4 minutes.");
  });

  it("shows the reset command for a forgotten password", async () => {
    fakeApi({ "GET /api/auth/me": fail(401, "Not authenticated") });
    renderApp("/login");

    await userEvent.click(await screen.findByRole("button", { name: "Forgot your password?" }));
    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    expect(screen.getByText(RESET_COMMAND)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
  });

  it("passes a browser that is still signed in straight through", async () => {
    fakeApi(signedIn());
    const { router } = renderApp("/login?next=%2Fprofile");

    await waitFor(() => expect(where(router)).toBe("/profile"));
  });

  it("forgets the previous session once on arrival, not again when next changes", async () => {
    const api = fakeApi({ "GET /api/auth/me": fail(401, "Not authenticated") });
    const { router } = renderApp("/login?next=%2Flibrary");
    await screen.findByLabelText("Username");
    await waitFor(() => expect(api.calls.filter((call) => call.path === "/api/auth/me")).toHaveLength(1));

    await act(() => router.navigate("/login?next=%2Factivity"));

    expect(api.calls.filter((call) => call.path === "/api/auth/me")).toHaveLength(1);
  });

  it("ignores a next that leaves the app", async () => {
    const api = fakeApi({ "GET /api/auth/me": fail(401, "Not authenticated") });
    const { router } = renderApp("/login?next=%2F%2Fevil.example");
    await signInChecked(api, 1);

    Object.assign(api.routes, signedIn(), { "POST /api/auth/login": ok(ME) });
    await userEvent.type(await screen.findByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "a long password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(where(router)).toBe("/"));
  });
});

describe("the layout", () => {
  it("shows the pages, the library count, the version, the service and who you are", async () => {
    fakeApi(signedIn());
    renderApp("/library");

    const nav = await screen.findByRole("navigation", { name: "Main" });
    await within(nav).findByText("v2.0.0");
    expect(within(nav).getByRole("link", { name: /Library/ })).toHaveAttribute("aria-current", "page");
    await waitFor(() => expect(within(nav).getByRole("link", { name: /Library/ })).toHaveTextContent("354"));
    expect(within(nav).getByRole("link", { name: "Dashboard" })).not.toHaveAttribute("aria-current");
    expect(await within(nav).findByText("Service healthy")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /admin/ })).toHaveAttribute("href", "/profile");
  });

  it("marks Accounts when an account needs a sign-in, and you while the generated password is in use", async () => {
    const account = {
      id: 1,
      name: "Alex (UK)",
      country_code: "uk",
      customer_name: "Alex",
      enabled: true,
      monitor_existing: true,
      needs_login: true,
      created_at: null,
      last_synced_at: null,
    };
    fakeApi(
      signedIn({
        "GET /api/status": ok(status({ accounts: [account] })),
        "GET /api/auth/me": ok({ ...ME, generated_password: true }),
      }),
    );
    renderApp("/");

    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(
      await within(nav).findByRole("link", { name: "Accounts, an account needs you to sign in again" }),
    ).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "admin, using the generated password" })).toBeInTheDocument();
  });

  it("shows the run in flight", async () => {
    fakeApi(
      signedIn({
        "GET /api/status": ok(
          status({
            scheduler: { ...status().scheduler, running: true },
            current_run: {
              run_id: 148,
              started_at: "2026-11-02T14:00:00+00:00",
              stage: "downloading",
              account: null,
              book: null,
              books_done: 2,
              books_total: 10,
              transfer: null,
            },
          }),
        ),
      }),
    );
    renderApp("/");

    expect(await screen.findByText("Syncing · book 3 of 10")).toBeInTheDocument();
  });

  it("goes to the sign-in when the session ends, coming back afterwards", async () => {
    const api = fakeApi(signedIn());
    const { router, client } = renderApp("/activity");
    await screen.findByRole("heading", { name: "Activity" });

    api.routes["GET /api/status"] = fail(401, "Not authenticated");
    api.routes["GET /api/auth/me"] = fail(401, "Not authenticated");
    await act(() => client.refetchQueries({ queryKey: ["status"] }));

    await screen.findByRole("heading", { name: "Sign in to Audible Sync" });
    expect(where(router)).toBe("/login?next=%2Factivity");
  });

  it("signs out", async () => {
    const api = fakeApi(signedIn({ "POST /api/auth/logout": () => new Response(null, { status: 204 }) }));
    const { router } = renderApp("/settings");
    const nav = await screen.findByRole("navigation", { name: "Main" });

    api.routes["GET /api/auth/me"] = fail(401, "Not authenticated");
    await userEvent.click(within(nav).getByRole("button", { name: "Sign out" }));

    await screen.findByRole("heading", { name: "Sign in to Audible Sync" });
    expect(where(router)).toBe("/login");
    expect(api.calls.some((call) => call.method === "POST" && call.path === "/api/auth/logout")).toBe(true);
  });

  it("keeps the page when a background check of the sign-in fails", async () => {
    const api = fakeApi(signedIn());
    const { client } = renderApp("/profile");
    await screen.findByRole("form", { name: "Password" });

    api.routes["GET /api/auth/me"] = fail(502, "Bad Gateway");
    await act(() => client.refetchQueries({ queryKey: ["me"] }));

    expect(screen.getByRole("form", { name: "Password" })).toBeInTheDocument();
    expect(screen.queryByText("Could not check your sign-in")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not load your sign-in")).not.toBeInTheDocument();
  });

  it("says why signing out failed, in words", async () => {
    fakeApi(signedIn({ "POST /api/auth/logout": fail(500, "database is locked") }));
    const { router } = renderApp("/settings");
    const nav = await screen.findByRole("navigation", { name: "Main" });

    await userEvent.click(within(nav).getByRole("button", { name: "Sign out" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not sign out");
    expect(alert).toHaveTextContent("database is locked");
    expect(where(router)).toBe("/settings");
  });

  it("reports a sign-out that gets no answer instead of failing silently", async () => {
    fakeApi(signedIn({ "POST /api/auth/logout": unreachable() }));
    renderApp("/settings");
    const nav = await screen.findByRole("navigation", { name: "Main" });

    await userEvent.click(within(nav).getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No response from the service");
  });

  it("answers an unknown address with the 404 page inside the layout", async () => {
    fakeApi(signedIn());
    renderApp("/libary");

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByText("/libary")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("opens the phone menu as a drawer, and closes it on picking a page", async () => {
    fakeApi(signedIn());
    renderApp("/");
    const button = await screen.findByRole("button", { name: "Open menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "nav-drawer");

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const drawer = document.getElementById("nav-drawer");
    expect(drawer).toHaveAttribute("open");
    expect(document.documentElement).toHaveClass("no-scroll");

    const links = within(drawer as HTMLElement);
    await userEvent.click(links.getByRole("link", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" });
    expect(drawer).not.toHaveAttribute("open");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.documentElement).not.toHaveClass("no-scroll");
  });

  it("closes the drawer on Escape", async () => {
    fakeApi(signedIn());
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Open menu" }));
    const drawer = document.getElementById("nav-drawer") as HTMLElement;

    fireEvent(drawer, new Event("cancel", { cancelable: true }));

    expect(drawer).not.toHaveAttribute("open");
  });
});

describe("the service unreachable", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("takes over the page when nothing answers, and comes back to where you were", async () => {
    const api = fakeApi({ "GET /api/auth/me": unreachable(), "GET /api/health": unreachable() });
    const { router } = renderApp("/profile");

    expect(await screen.findByRole("heading", { name: "Cannot reach Audible Sync" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Trying again in \d+ seconds?/);

    Object.assign(api.routes, signedIn());
    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));

    expect(await screen.findByRole("heading", { name: "Your sign-in" })).toBeInTheDocument();
    expect(where(router)).toBe("/profile");
  });

  it("does not take over for a single dropped request", async () => {
    const api = fakeApi(signedIn());
    const { client } = renderApp("/profile");
    await screen.findByRole("heading", { name: "Your sign-in" });

    api.routes["GET /api/status"] = unreachable();
    await act(() => client.refetchQueries({ queryKey: ["status"] }));

    await waitFor(() => expect(api.calls.filter((call) => call.path === "/api/health").length).toBeGreaterThan(1));
    expect(screen.queryByRole("heading", { name: "Cannot reach Audible Sync" })).not.toBeInTheDocument();
  });

  it("checks again with backoff, counting down", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const check = vi.fn().mockResolvedValue(false);
    const client = makeQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useReachability(check), { wrapper });

    await act(() => result.current.retryNow());
    await waitFor(() => expect(result.current.down).toBe(true));
    expect(result.current.secondsLeft).toBe(retryDelay(0));

    await act(() => vi.advanceTimersByTimeAsync(retryDelay(0) * 1000));
    await waitFor(() => expect(check).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.secondsLeft).toBe(retryDelay(1)));

    check.mockResolvedValue(true);
    await act(() => vi.advanceTimersByTimeAsync(retryDelay(1) * 1000));
    await waitFor(() => expect(result.current.down).toBe(false));
  });
});

describe("basenameFrom", () => {
  it.each([
    ["http://nas:8080/", "/"],
    ["http://my.domain/audible-sync/", "/audible-sync"],
    ["http://my.domain/apps/audible-sync/", "/apps/audible-sync"],
  ])("%s gives %s", (base, expected) => {
    expect(basenameFrom(base)).toBe(expected);
  });
});
