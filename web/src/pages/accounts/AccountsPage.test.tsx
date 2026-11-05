import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fail, fakeApi, json, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { type Account, account, counts } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import type { Login } from "./login";

const UK = account();
const US = account({ id: 2, name: "Alex (US)", country_code: "us", enabled: false });
const OLD = account({
  id: 3,
  name: "Audible",
  country_code: "",
  customer_name: null,
  needs_login: true,
  last_synced_at: null,
});

const MARKETPLACES = [
  { country_code: "de", domain: "de", name: "Germany" },
  { country_code: "us", domain: "com", name: "United States" },
  { country_code: "uk", domain: "co.uk", name: "United Kingdom" },
];

const NOW = Date.parse("2026-11-02T12:00:00Z");

function stats(accounts: Account[]) {
  return {
    accounts: accounts.map((entry) => ({
      account: entry,
      books: counts({ downloaded: 41, waiting_download: 7 }),
      bytes_on_disk: 0,
      last_run: null,
    })),
    books: counts({ downloaded: 41 }),
    bytes_on_disk: 0,
    last_run: null,
    next_run_at: null,
  };
}

function page(accounts: Account[] = [UK, US, OLD], extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/accounts": ok(accounts),
      "GET /api/marketplaces": ok(MARKETPLACES),
      "GET /api/stats": ok(stats(accounts)),
      ...extra,
    }),
  );
}

function codeLogin(overrides: Partial<Login> = {}): Login {
  return {
    login_id: "abc",
    method: "code",
    status: "pending",
    expires_at: new Date(NOW + 600_000).toISOString(),
    code: "KQ7XM",
    verification_url: "https://www.amazon.co.uk/code",
    ...overrides,
  };
}

/** Fake time from NOW, still advancing on its own so the app's awaits settle. */
function fakeTime() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
}

const card = (name: string) => screen.getByRole("article", { name });

afterEach(() => {
  vi.useRealTimers();
});

describe("Accounts", () => {
  it("shows each account with its state, books and actions", async () => {
    page();
    renderApp("/accounts");

    await screen.findByRole("heading", { name: "Alex (UK)" });
    const uk = within(card("Alex (UK)"));
    expect(uk.getByText("Active")).toBeInTheDocument();
    // The date in the machine's locale
    expect(await uk.findByText(/^Alex · audible\.co\.uk · added .*2026$/)).toBeInTheDocument();
    await waitFor(() => expect(uk.getByText("48")).toBeInTheDocument());
    expect(uk.getByRole("button", { name: "Sign in again" })).toBeInTheDocument();
    expect(uk.getByRole("switch", { name: "Sync Alex (UK)" })).toHaveAttribute("aria-checked", "true");

    expect(within(card("Alex (US)")).getByText("Paused")).toBeInTheDocument();
    const old = within(card("Audible"));
    expect(old.getByText("Needs sign-in")).toBeInTheDocument();
    expect(old.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(old.getByText("Never")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: 'Downloads refused with "License not granted"?' })).toBeInTheDocument();
  });

  it("offers to add the first account when there is none", async () => {
    page([]);
    renderApp("/accounts");

    expect(await screen.findByRole("heading", { name: "No accounts yet" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add an Audible account" }));
    expect(screen.getByRole("dialog", { name: "Add an Audible account" })).toBeInTheDocument();
  });

  it("pauses an account at once and puts it back when the service refuses", async () => {
    const api = page([UK], { "PATCH /api/accounts/1": fail(500, "database is locked") });
    renderApp("/accounts");
    const sync = await screen.findByRole("switch", { name: "Sync Alex (UK)" });

    await userEvent.click(sync);
    expect(api.sent("PATCH /api/accounts/1")).toEqual([{ enabled: false }]);
    expect(await screen.findByText("Could not pause the account")).toBeInTheDocument();
    await waitFor(() => expect(sync).toHaveAttribute("aria-checked", "true"));
  });

  it("renames an account from its menu", async () => {
    const api = page([UK], { "PATCH /api/accounts/1": ok({ ...UK, name: "Home" }) });
    renderApp("/accounts");

    const more = await screen.findByRole("button", { name: "More actions for Alex (UK)" });
    await userEvent.click(more);
    expect(screen.getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Remove account" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(more).toHaveFocus();

    await userEvent.click(more);
    await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const dialog = screen.getByRole("dialog", { name: "Rename account" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    await userEvent.clear(name);
    await userEvent.type(name, "  Home ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(api.sent("PATCH /api/accounts/1")).toEqual([{ name: "Home" }]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("removes an account, deregistering its device, and says when a run is on it", async () => {
    let busy = true;
    const api = page([UK], {
      "DELETE /api/accounts/1": () =>
        busy
          ? json(409, { detail: "Alex (UK) is being synced; cancel the run first" })
          : json(200, { status: "deleted" }),
    });
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "More actions for Alex (UK)" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Remove account" }));
    const dialog = screen.getByRole("dialog", { name: "Remove Alex (UK)?" });
    expect(within(dialog).getByText(/The 41 books already in your audiobooks folder stay on disk/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /Also remove this device/ })).toBeChecked();

    await userEvent.click(within(dialog).getByRole("button", { name: "Remove account" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("A run is syncing Alex (UK) right now");

    busy = false;
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Also remove this device/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove account" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const deletes = api.calls.filter((call) => call.method === "DELETE").map((call) => new URL(call.url).search);
    expect(deletes).toEqual(["?deregister=true", "?deregister=false"]);
  });

  it("adds an account with a code, polling every 3 s until it is confirmed", async () => {
    const user = fakeTime();
    const DE = account({ id: 4, name: "Alex (DE)", country_code: "de" });
    let answer: Login = codeLogin();
    const api = page([UK], {
      "POST /api/accounts/login": () => json(202, codeLogin()),
      "GET /api/accounts/login/abc": () => json(200, answer),
      "POST /api/sync": () => json(202, { status: "started" }),
    });
    renderApp("/accounts");

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    const marketplace = within(dialog).getByRole("combobox", { name: "Marketplace" });
    await waitFor(() => expect(marketplace).toHaveValue("us"));
    await user.selectOptions(marketplace, "de");
    await user.type(within(dialog).getByRole("textbox", { name: /Name/ }), "Alex (DE)");
    await user.click(within(dialog).getByRole("checkbox", { name: /Download the books already/ }));
    await user.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));

    expect(await within(dialog).findByText("Code: KQ7XM")).toBeInTheDocument();
    expect(api.sent("POST /api/accounts/login")).toEqual([
      { country_code: "de", method: "code", name: "Alex (DE)", monitor_existing: false, deregister_previous: true },
    ]);
    expect(within(dialog).getByRole("link", { name: "Open amazon.co.uk/code" })).toHaveAttribute(
      "href",
      "https://www.amazon.co.uk/code",
    );
    expect(within(dialog).getByRole("status")).toHaveTextContent("10:00 left");

    const polls = () => api.calls.filter((call) => call.path === "/api/accounts/login/abc").length;
    expect(polls()).toBe(0);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(polls()).toBe(1));
    expect(within(dialog).getByRole("status")).toHaveTextContent("9:57 left");

    answer = codeLogin({ status: "confirmed", account: DE });
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await within(dialog).findByText("Alex (DE) is connected")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.calls.some((call) => call.method === "POST" && call.path === "/api/sync")).toBe(true);
  });

  it("says when the code has expired and gets a new one", async () => {
    const user = fakeTime();
    let started = 0;
    const api = page([UK], {
      "POST /api/accounts/login": () => {
        started += 1;
        return json(
          202,
          codeLogin({
            login_id: `login-${started}`,
            code: started === 1 ? "KQ7XM" : "ABCDE",
            expires_at: new Date(Date.now() + 600_000).toISOString(),
          }),
        );
      },
      // Pending until its time is up, then expired, as the service computes it on read
      "GET /api/accounts/login/login-1": () =>
        json(200, codeLogin({ login_id: "login-1", status: Date.now() < NOW + 600_000 ? "pending" : "expired" })),
    });
    renderApp("/accounts");

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await user.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await within(dialog).findByText("Code: KQ7XM");

    await act(() => vi.advanceTimersByTimeAsync(599_000));
    expect(within(dialog).getByRole("status")).toHaveTextContent("0:01 left");
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await within(dialog).findByText("This code has expired")).toBeInTheDocument();
    // No more polling once the service says it is over
    const polls = api.calls.filter((call) => call.path === "/api/accounts/login/login-1").length;
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(api.calls.filter((call) => call.path === "/api/accounts/login/login-1").length).toBe(polls);

    await user.click(within(dialog).getByRole("button", { name: "Get a new code" }));
    expect(await within(dialog).findByText("Code: ABCDE")).toBeInTheDocument();
    expect(within(dialog).queryByText("This code has expired")).not.toBeInTheDocument();
  });

  it("keeps polling after a failed poll", async () => {
    const user = fakeTime();
    const answers = [json(502, { detail: "Bad Gateway" }), json(200, codeLogin({ status: "confirmed", account: UK }))];
    page([UK], {
      "POST /api/accounts/login": () => json(202, codeLogin()),
      "GET /api/accounts/login/abc": () =>
        answers.shift() ?? json(200, codeLogin({ status: "confirmed", account: UK })),
    });
    renderApp("/accounts");

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await user.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await within(dialog).findByText("Code: KQ7XM");

    await act(() => vi.advanceTimersByTimeAsync(3000));
    await waitFor(() => expect(answers).toHaveLength(1));
    expect(within(dialog).getByText("Waiting for you to enter the code…")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await within(dialog).findByText("Alex (UK) is connected")).toBeInTheDocument();
  });

  it("reads the accounts again when a dialog with a login in it is closed", async () => {
    const api = page([UK], { "POST /api/accounts/login": () => json(202, codeLogin()) });
    renderApp("/accounts");
    const reads = () => api.calls.filter((call) => call.method === "GET" && call.path === "/api/accounts").length;

    await userEvent.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await within(dialog).findByText("Code: KQ7XM");
    const before = reads();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(reads()).toBe(before + 1));
  });

  it("reads the accounts again when the polled status's accounts change", async () => {
    fakeTime();
    const DE = account({ id: 4, name: "Alex (DE)", country_code: "de" });
    let accounts = [UK];
    const api = page([UK], {
      "GET /api/accounts": () => json(200, accounts),
      "GET /api/status": () => json(200, status({ accounts })),
    });
    renderApp("/accounts");
    await screen.findByRole("heading", { name: "Alex (UK)" });

    // A code login confirmed after its dialog was closed
    accounts = [UK, DE];
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByRole("heading", { name: "Alex (DE)" })).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/accounts").length).toBeGreaterThan(1);
  });

  it("starts a new code afresh even if the service reused the login's id", async () => {
    const user = fakeTime();
    let expired = true;
    page([UK], {
      "POST /api/accounts/login": () => json(202, codeLogin()),
      "GET /api/accounts/login/abc": () => json(200, codeLogin({ status: expired ? "expired" : "pending" })),
    });
    renderApp("/accounts");

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await user.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await within(dialog).findByText("This code has expired");

    expired = false;
    await user.click(within(dialog).getByRole("button", { name: "Get a new code" }));
    expect(await within(dialog).findByText("Waiting for you to enter the code…")).toBeInTheDocument();
  });

  it("shows a failed sign-in's error and falls back to the browser sign-in", async () => {
    const user = fakeTime();
    let pasted = 0;
    const api = page([UK], {
      "POST /api/accounts/login": async (request) => {
        const body = (await request.json()) as { method: string };
        return body.method === "browser"
          ? json(202, {
              login_id: "web",
              method: "browser",
              status: "pending",
              expires_at: codeLogin().expires_at,
              url: "https://www.amazon.de/ap/signin?x=1",
            })
          : json(202, codeLogin());
      },
      "GET /api/accounts/login/abc": () =>
        json(200, codeLogin({ status: "failed", error: "Amazon refused the device" })),
      "POST /api/accounts/login/web": () => {
        pasted += 1;
        return pasted === 1
          ? json(400, { detail: "That address carries no authorization code" })
          : json(201, {
              login_id: "web",
              method: "browser",
              status: "confirmed",
              expires_at: codeLogin().expires_at,
              account: account({ id: 4, name: "Alex (DE)" }),
            });
      },
    });
    renderApp("/accounts");

    await user.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await user.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await within(dialog).findByText("Code: KQ7XM");
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await within(dialog).findByText("Amazon refused the device")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Use browser sign-in" }));
    expect(await within(dialog).findByRole("link", { name: "Open Amazon sign-in" })).toHaveAttribute(
      "href",
      "https://www.amazon.de/ap/signin?x=1",
    );
    expect(api.sent("POST /api/accounts/login").map((body) => (body as { method: string }).method)).toEqual([
      "code",
      "browser",
    ]);

    const address = within(dialog).getByRole("textbox", { name: /page not found/ });
    await user.type(address, "https://www.amazon.de/ap/maplanding?nothing");
    await user.click(within(dialog).getByRole("button", { name: "Finish sign-in" }));
    expect(await within(dialog).findByText("That address carries no authorization code")).toBeInTheDocument();
    expect(address).toHaveAttribute("aria-invalid", "true");

    await user.clear(address);
    await user.type(address, "https://www.amazon.de/ap/maplanding?openid.oa2.authorization_code=x");
    await user.click(within(dialog).getByRole("button", { name: "Finish sign-in" }));
    expect(await within(dialog).findByText("Alex (DE) is connected")).toBeInTheDocument();
    expect(api.sent("POST /api/accounts/login/web")[1]).toEqual({
      response_url: "https://www.amazon.de/ap/maplanding?openid.oa2.authorization_code=x",
    });
  });

  it("keeps the browser sign-in open when finishing it gets no proper answer", async () => {
    let tries = 0;
    page([UK], {
      "POST /api/accounts/login": async (request) => {
        const body = (await request.json()) as { method: string };
        return body.method === "browser"
          ? json(202, { ...codeLogin({ login_id: "web", method: "browser", url: "https://a" }), code: null })
          : json(202, codeLogin());
      },
      "POST /api/accounts/login/web": () => {
        tries += 1;
        return tries === 1
          ? json(500, { detail: "Internal Server Error" })
          : json(201, { ...codeLogin({ login_id: "web", method: "browser", status: "confirmed" }), account: UK });
      },
    });
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "browser sign-in" }));
    await userEvent.type(await within(dialog).findByRole("textbox", { name: /page not found/ }), "https://x/y");
    await userEvent.click(within(dialog).getByRole("button", { name: "Finish sign-in" }));

    expect(await within(dialog).findByText(/Could not finish the sign-in: Internal Server Error/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Start again" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Finish sign-in" }));
    expect(await within(dialog).findByText("Alex (UK) is connected")).toBeInTheDocument();
  });

  it("offers to start again when the browser sign-in has gone", async () => {
    const api = page([UK], {
      "POST /api/accounts/login": async (request) => {
        const body = (await request.json()) as { method: string };
        return body.method === "browser"
          ? json(202, { ...codeLogin({ login_id: "web", method: "browser", url: "https://a" }), code: null })
          : json(202, codeLogin());
      },
      "GET /api/accounts/login/abc": () => json(200, codeLogin()),
      "POST /api/accounts/login/web": fail(404, "No such login, or it has expired; start again"),
    });
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    await userEvent.click(await within(dialog).findByRole("button", { name: "browser sign-in" }));
    await userEvent.type(await within(dialog).findByRole("textbox", { name: /page not found/ }), "https://x/y");
    await userEvent.click(within(dialog).getByRole("button", { name: "Finish sign-in" }));
    expect(await within(dialog).findByText("No such login, or it has expired; start again")).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(api.sent("POST /api/accounts/login")).toHaveLength(3));
  });

  it("signs an account in again with its marketplace fixed", async () => {
    const api = page([UK], { "POST /api/accounts/login": () => json(202, codeLogin()) });
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "Sign in again" }));
    const dialog = screen.getByRole("dialog", { name: "Sign in again: Alex (UK)" });
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument();
    expect(await within(dialog).findByText("United Kingdom (audible.co.uk)")).toBeInTheDocument();
    expect(within(dialog).getByText(/Your library and download history are kept/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("checkbox", { name: /Remove the old device/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));

    await within(dialog).findByText("Code: KQ7XM");
    expect(api.sent("POST /api/accounts/login")).toEqual([
      { country_code: "uk", method: "code", account_id: 1, monitor_existing: true, deregister_previous: false },
    ]);
  });

  it("lets the placeholder account choose its marketplace", async () => {
    page([OLD]);
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    const dialog = screen.getByRole("dialog", { name: "Sign in again: Audible" });
    expect(within(dialog).getByRole("combobox", { name: "Marketplace" })).toBeInTheDocument();
    // It has no device to remove
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("says when Amazon will not issue a code", async () => {
    page([UK], { "POST /api/accounts/login": fail(502, "Could not reach Amazon: timed out") });
    renderApp("/accounts");

    await userEvent.click(await screen.findByRole("button", { name: "Add account" }));
    const dialog = screen.getByRole("dialog", { name: "Add an Audible account" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Get sign-in code" }));
    expect(await within(dialog).findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent(
      "Could not start the sign-in: Could not reach Amazon: timed out",
    );
  });
});
