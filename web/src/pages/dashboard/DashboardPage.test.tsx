import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { useRefreshDuringRun } from "../../api/queries";
import { makeQueryClient } from "../../api/queryClient";
import { fakeApi, ME, ok, signedIn, status } from "../../test/fakeApi";
import { account, book, counts, run } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { DURING_RUN } from "./DashboardPage";

const UK = account();
const US = account({ id: 2, name: "Alex (US)", country_code: "us" });

const STATS = {
  accounts: [
    {
      account: UK,
      books: counts({ downloaded: 271, waiting_download: 17, unavailable: 16, failed: 2 }),
      bytes_on_disk: 131_400_000_000,
      last_run: run(),
    },
    {
      account: US,
      books: counts({ downloaded: 41, waiting_download: 4 }),
      bytes_on_disk: 16_800_000_000,
      last_run: null,
    },
  ],
  books: counts({ downloaded: 312, waiting_download: 21, unavailable: 16, failed: 2 }, 15),
  bytes_on_disk: 148_200_000_000,
  last_run: run(),
  next_run_at: "2026-11-02T18:00:00+00:00",
};

const QUEUE = {
  items: [
    { position: 1, account_id: 1, book: book({ id: 501, title: "Morning Star", date_added: "2026-11-01T00:00:00Z" }) },
    { position: 2, account_id: 2, book: book({ id: 502, title: "Caliban’s War", authors: ["James S. A. Corey"] }) },
  ],
  total: 20,
  waiting: 21,
  max_download: 10,
};

function dashboard(extra = {}) {
  return signedIn({
    "GET /api/status": ok(status({ accounts: [UK, US] })),
    "GET /api/stats": ok(STATS),
    "GET /api/queue": ok(QUEUE),
    "GET /api/books": (request: Request) => {
      const query = new URL(request.url).searchParams;
      if (query.get("status") === "failed") {
        return Response.json({
          items: [book({ id: 9, title: "Golden Son", status: "failed", last_error: "ffmpeg exited with status 1" })],
          total: 2,
          page: 1,
          page_size: 1,
        });
      }
      if (query.get("status") === "downloaded") {
        return Response.json({
          items: [book({ id: 7, title: "Dune", authors: ["Frank Herbert"], status: "downloaded" })],
          total: 312,
          page: 1,
          page_size: 6,
        });
      }
      return Response.json({ items: [], total: 354, page: 1, page_size: 1 });
    },
    "GET /api/sync/runs": ok({
      items: [run(), run({ id: 147, outcome: "partial", books_added: 0, books_downloaded: 5, books_failed: 1 })],
      total: 2,
    }),
    ...extra,
  });
}

describe("the Dashboard", () => {
  it("shows the totals, each linking to the library filtered to it", async () => {
    fakeApi(dashboard());
    renderApp("/");

    const summary = await screen.findByRole("region", { name: "Library summary" });
    const downloaded = await within(summary).findByRole("link", { name: /Downloaded/ });
    await waitFor(() => expect(downloaded).toHaveTextContent("312"));
    expect(downloaded).toHaveTextContent("148 GB on disk");
    expect(downloaded).toHaveAttribute("href", "/library?status=downloaded");
    expect(within(summary).getByRole("link", { name: /In library/ })).toHaveTextContent("336 monitored");
    expect(within(summary).getByRole("link", { name: /Failed/ })).toHaveAttribute("href", "/library?status=failed");
    // 21 waiting in all, but the queue says 21 of those will run; the tile says which
    expect(within(summary).getByRole("link", { name: /Waiting/ })).toHaveTextContent("21 queued for the next run");
  });

  it("counts only the queued books as queued, not the unmonitored ones", async () => {
    fakeApi(dashboard({ "GET /api/queue": ok({ ...QUEUE, items: [], total: 0, waiting: 0 }) }));
    renderApp("/");

    const summary = await screen.findByRole("region", { name: "Library summary" });
    const waiting = within(summary).getByRole("link", { name: /Waiting/ });
    await waitFor(() => expect(waiting).toHaveTextContent("0 queued for the next run"));
    expect(waiting).toHaveTextContent("21");
  });

  it("gives the schedule and the next run, and starts a sync", async () => {
    const api = fakeApi(dashboard({ "POST /api/sync": ok({ status: "started" }) }));
    renderApp("/");

    expect(await screen.findByText("2 accounts · scheduled every 6 hours")).toBeInTheDocument();
    expect(screen.getByText(/^Next run/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));

    expect(await screen.findByText("Sync started.")).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === "POST" && call.path === "/api/sync")).toBe(true);
  });

  it("shows each account with its books by status", async () => {
    fakeApi(dashboard());
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Accounts" });
    expect(await within(card).findByText("Alex (UK)")).toBeInTheDocument();
    expect(within(card).getByText("306 books · 131 GB")).toBeInTheDocument();
    expect(within(card).getByText("16 unavailable")).toBeInTheDocument();
    expect(within(card).getByText("Not run yet")).toBeInTheDocument();
    // Nothing downloading, so no such segment
    expect(within(card).queryByText(/downloading/)).not.toBeInTheDocument();
  });

  it("counts a book being downloaded in its account's breakdown", async () => {
    const busy = { ...STATS.accounts[0], books: counts({ downloaded: 271, downloading: 1, waiting_download: 16 }) };
    fakeApi(dashboard({ "GET /api/stats": ok({ ...STATS, accounts: [busy] }) }));
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Accounts" });
    expect(await within(card).findByText("1 downloading")).toBeInTheDocument();
  });

  it("marks a run the service never closed as interrupted", async () => {
    fakeApi(
      dashboard({
        "GET /api/sync/runs": ok({ items: [run({ id: 140, outcome: "running", finished_at: null })], total: 1 }),
      }),
    );
    renderApp("/");

    const runs = await screen.findByRole("region", { name: "Recent runs" });
    expect(await within(runs).findByText("Interrupted")).toBeInTheDocument();
    expect(within(runs).getByText("Interrupted: the service stopped during this run")).toBeInTheDocument();
  });

  it("lists what runs next, with how many more are waiting", async () => {
    fakeApi(dashboard());
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Up next" });
    const first = await within(card).findByRole("link", { name: /Morning Star/ });
    expect(first).toHaveAttribute("href", "/library/501");
    // The test run's locale decides the order of day and month
    expect(first).toHaveTextContent(/bought (1 Nov|Nov 1)/);
    // Two accounts, so each row names its marketplace
    expect(within(card).getByRole("link", { name: /Caliban’s War/ })).toHaveTextContent("US");
    expect(within(card).getByText(/up to 10 books per account/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "+19 more waiting" })).toHaveAttribute(
      "href",
      "/library?status=waiting_download&monitored=true",
    );
  });

  it("shows the recent downloads and runs", async () => {
    fakeApi(dashboard());
    renderApp("/");

    const recent = await screen.findByRole("region", { name: "Recently downloaded" });
    expect(await within(recent).findByRole("link", { name: /Dune/ })).toHaveAttribute("href", "/library/7");
    const runs = await screen.findByRole("region", { name: "Recent runs" });
    expect(await within(runs).findByText("5 downloaded · 1 failed")).toBeInTheDocument();
    expect(within(runs).getAllByText(/Alex \(UK\)/).length).toBeGreaterThan(0);
    // Cut short when long, so the line has its whole text as a title
    const line = within(runs).getAllByText(/· Alex \(UK\)$/)[0];
    expect(line).toHaveAttribute("title", line?.textContent);
  });

  it("lists what needs attention and retries the unavailable books", async () => {
    const api = fakeApi(dashboard({ "POST /api/books/retry-unavailable": ok({ requeued: 12 }) }));
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).getByText("16 books unavailable")).toBeInTheDocument();
    expect(await within(card).findByText(/Golden Son: ffmpeg exited with status 1/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Review failed books" })).toHaveAttribute(
      "href",
      "/library?status=failed",
    );

    await userEvent.click(within(card).getByRole("button", { name: "Retry unavailable" }));
    expect(await screen.findByText("12 books queued again for the next run.")).toBeInTheDocument();
    expect(api.sent("POST /api/books/retry-unavailable")).toEqual([{}]);
  });

  it("asks for a sign-in on an account that needs one", async () => {
    const stuck = account({ ...US, needs_login: true });
    fakeApi(dashboard({ "GET /api/status": ok(status({ accounts: [UK, stuck] })) }));
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Needs attention" });
    expect(within(card).getByText("Alex (US) needs a sign-in")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/accounts");
  });

  it("shows the run in flight, with the book, its progress and Cancel run", async () => {
    const api = fakeApi(
      dashboard({
        "GET /api/status": ok(
          status({
            accounts: [UK, US],
            scheduler: { ...status().scheduler, running: true },
            current_run: {
              run_id: 148,
              started_at: "2026-11-02T14:00:00+00:00",
              stage: "downloading",
              account: { id: 1, name: "Alex (UK)", country_code: "uk" },
              book: { id: 42, asin: "B00I2VWW5U", title: "Red Rising" },
              books_done: 2,
              books_total: 10,
              transfer: { desc: "Red Rising", bytes: 412_000_000, total: 688_000_000 },
            },
          }),
        ),
        "GET /api/books/42": ok(book({ id: 42, title: "Red Rising", status: "downloading" })),
        "POST /api/sync/cancel": ok({ status: "cancelling" }),
      }),
    );
    renderApp("/");

    const card = await screen.findByRole("region", { name: "Run in progress" });
    expect(within(card).getByText("Downloading")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Red Rising" })).toHaveAttribute("href", "/library/42");
    expect(await within(card).findByText("by Pierce Brown")).toBeInTheDocument();
    expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
    expect(within(card).getByText("412 MB of 688 MB")).toBeInTheDocument();
    expect(within(card).getByText("Book 3 of 10 this run")).toBeInTheDocument();
    expect(within(card).getByText(/Run #148 · Alex \(UK\)/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    await waitFor(() => expect(api.calls.some((call) => call.path === "/api/sync/cancel")).toBe(true));
  });

  it("welcomes a new install instead, with the totals dimmed at zero", async () => {
    fakeApi(
      dashboard({
        "GET /api/status": ok(status({ accounts: [] })),
        "GET /api/auth/me": ok({ ...ME, generated_password: true }),
        "GET /api/stats": ok({ ...STATS, accounts: [], books: counts({}), bytes_on_disk: 0, last_run: null }),
      }),
    );
    renderApp("/");

    expect(await screen.findByRole("heading", { name: "Welcome to Audible Sync" })).toBeInTheDocument();
    expect(screen.getByText("No accounts yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add an Audible account" })).toHaveAttribute("href", "/accounts");
    expect(screen.getByText("You are using the generated password.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Up next" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Recent runs" })).not.toBeInTheDocument();
    expect(await screen.findByText("No books yet")).toBeInTheDocument();
  });
});

describe("useRefreshDuringRun", () => {
  it("refetches when the run takes up another book, even one refused without using a slot", () => {
    const client = makeQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const live = (bookId: number, done: number) =>
      status({
        current_run: {
          run_id: 148,
          started_at: "2026-11-02T14:00:00+00:00",
          stage: "downloading",
          account: null,
          book: { id: bookId, asin: "B0", title: "x" },
          books_done: done,
          books_total: 10,
          transfer: null,
        },
      });
    const hook = renderHook(({ value }) => useRefreshDuringRun(value, DURING_RUN), {
      wrapper,
      initialProps: { value: live(1, 0) },
    });

    hook.rerender({ value: live(1, 0) });
    expect(invalidate).not.toHaveBeenCalled();

    // Book 1 refused: no slot used, so books_done stays 0, but the run moved on
    hook.rerender({ value: live(2, 0) });
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
      ["stats"],
      ["books", "recent"],
      ["queue"],
    ]);
  });
});
