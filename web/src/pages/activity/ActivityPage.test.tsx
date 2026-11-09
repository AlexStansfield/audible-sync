import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeApi, json, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { account, run, type SyncRun } from "../../test/fixtures";
import { renderApp } from "../../test/render";

const UK = account();
const US = account({ id: 2, name: "Alex (US)", country_code: "us" });

const RUNS: SyncRun[] = [
  run({ id: 147, account_id: 2, books_seen: 0, books_added: 0, books_downloaded: 0 }),
  run({ id: 145, outcome: "partial", books_seen: 6, books_added: 5, books_downloaded: 5, books_failed: 1 }),
  run({ id: 141, outcome: "failed", error: "Credentials rejected by Audible", books_recorded: false }),
];

const BOOKS_145 = {
  run_id: 145,
  recorded: true,
  downloaded: [
    {
      book_id: 7,
      asin: "B07",
      title: "Golden Son",
      cover_url: null,
      error: null,
      bytes: 611e6,
      seconds: 542,
      finished_at: "2026-11-02T08:10:00+00:00",
    },
  ],
  failed: [
    {
      book_id: 9,
      asin: "B09",
      title: "The Ringworld Engineers",
      cover_url: null,
      error: "ffmpeg exited with status 1",
      bytes: null,
      seconds: 12,
      finished_at: "2026-11-02T08:10:00+00:00",
    },
  ],
  refused: [],
};

const LOGS = [
  { time: "2026-11-02T14:00:00+00:00", level: "INFO", logger: "src.scheduler", message: "Starting scheduled run" },
  { time: "2026-11-02T14:06:42+00:00", level: "WARNING", logger: "src.downloader", message: "Licence denied" },
];

function page(extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/status": ok(status({ accounts: [UK, US] })),
      "GET /api/sync/runs": ok({ items: RUNS, total: RUNS.length }),
      "GET /api/sync/runs/145/books": ok(BOOKS_145),
      "GET /api/sync/runs/141/books": ok({ run_id: 141, recorded: false, downloaded: [], failed: [], refused: [] }),
      "GET /api/logs": ok({ items: LOGS }),
      ...extra,
    }),
  );
}

const rowOf = (id: number) => screen.getByRole("button", { name: `Books in run ${id}` }).closest("tr") as HTMLElement;

afterEach(() => {
  vi.useRealTimers();
});

describe("Activity", () => {
  it("lists the runs with their outcome, account, counts and note", async () => {
    page();
    renderApp("/activity");

    expect(await screen.findByText("3 runs recorded")).toBeInTheDocument();
    const partial = within(rowOf(145));
    expect(partial.getByText("Partial")).toBeInTheDocument();
    expect(partial.getByText("Alex (UK)")).toBeInTheDocument();
    expect(partial.getByText("12m")).toBeInTheDocument();
    expect(within(rowOf(147)).getByText("Alex (US)")).toBeInTheDocument();
    expect(within(rowOf(141)).getByText("Credentials rejected by Audible")).toBeInTheDocument();
    expect(screen.getByText("1–3 of 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("shows the run in flight live and an abandoned running row as interrupted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(Date.parse("2026-11-02T14:20:00Z"));
    page({
      "GET /api/status": ok(
        status({
          accounts: [UK],
          scheduler: { enabled: true, interval_minutes: 360, next_run_at: null, running: true },
          current_run: {
            run_id: 150,
            started_at: "2026-11-02T14:00:00+00:00",
            stage: "downloading",
            account: null,
            book: { id: 7, asin: "B07", title: "Red Rising" },
            full_refresh: false,
            books_done: 1,
            books_total: 10,
            transfer: null,
          },
        }),
      ),
      "GET /api/sync/runs": ok({
        items: [
          run({
            id: 150,
            outcome: "running",
            started_at: "2026-11-02T14:00:00+00:00",
            finished_at: null,
          }),
          run({ id: 149, outcome: "running", finished_at: null }),
        ],
        total: 2,
      }),
    });
    renderApp("/activity");

    await waitFor(() => expect(within(rowOf(150)).getByText("Downloading Red Rising")).toBeInTheDocument());
    const duration = () => within(rowOf(150)).getByText(/^20m( \d+s)?$/).textContent;
    const first = duration();
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(duration()).not.toBe(first));
    expect(within(rowOf(149)).getByText("Interrupted")).toBeInTheDocument();
  });

  it("opens a run onto its books, and says when a run recorded none", async () => {
    const { router } = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Books in run 145" }));

    expect(screen.getByRole("button", { name: "Books in run 145" })).toHaveAttribute("aria-expanded", "true");
    expect(router.state.location.search).toBe("?run=145");
    expect(await screen.findByRole("heading", { name: "Downloaded (1)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Failed (1)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Refused by Audible (0)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Golden Son/ })).toHaveAttribute("href", "/library/7");
    expect(screen.getByText("9m 2s · 611 MB")).toBeInTheDocument();
    expect(screen.getByText("ffmpeg exited with status 1")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Books in run 141" }));
    expect(await screen.findByText(/from before Audible Sync recorded each book/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Downloaded (1)" })).not.toBeInTheDocument();
  });

  it("opens the run a link names", async () => {
    page();
    renderApp("/activity?run=145");
    expect(await screen.findByRole("heading", { name: "Downloaded (1)" })).toBeInTheDocument();
  });

  it("loads more runs by offset", async () => {
    const all = Array.from({ length: 25 }, (_, i) => run({ id: 200 - i }));
    const api = page({
      "GET /api/sync/runs": (request: Request) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get("offset"));
        const limit = Number(url.searchParams.get("limit"));
        return json(200, { items: all.slice(offset, offset + limit), total: all.length });
      },
    });
    renderApp("/activity");

    expect(await screen.findByText("1–20 of 25")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("1–25 of 25")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    const offsets = api.calls
      .filter((call) => call.path === "/api/sync/runs")
      .map((call) => new URL(call.url).searchParams.get("offset"));
    expect(offsets).toEqual(["0", "20"]);
  });

  it("shows a row once when a new run pushed it onto the next page too", async () => {
    // 21 runs, then a new one: page two (offset 20) starts with what page one ended on.
    // Page one is not read again, so the new run is not among the 21 rows (200 to 180)
    const before = Array.from({ length: 21 }, (_, i) => run({ id: 200 - i }));
    let all = before;
    page({
      "GET /api/sync/runs": (request: Request) => {
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        return json(200, { items: all.slice(offset, offset + 20), total: all.length });
      },
    });
    renderApp("/activity");
    await screen.findByText("1–20 of 21");

    all = [run({ id: 201 }), ...before];
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^Books in run / })).toHaveLength(21));
    expect(screen.getAllByRole("button", { name: "Books in run 181" })).toHaveLength(1);
  });

  it("shows a run as soon as it starts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let started = false;
    page({
      "GET /api/status": () =>
        json(
          200,
          status({
            accounts: [UK],
            scheduler: { enabled: true, interval_minutes: 360, next_run_at: null, running: started },
            current_run: started
              ? {
                  run_id: 150,
                  started_at: new Date().toISOString(),
                  stage: "syncing",
                  account: null,
                  book: null,
                  full_refresh: false,
                  books_done: 0,
                  books_total: 0,
                  transfer: null,
                }
              : null,
          }),
        ),
      "GET /api/sync/runs": () =>
        json(200, {
          items: started ? [run({ id: 150, outcome: "running", finished_at: null }), ...RUNS] : RUNS,
          total: RUNS.length + (started ? 1 : 0),
        }),
    });
    renderApp("/activity");
    await screen.findByText("3 runs recorded");

    started = true;
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText("Reading the library from Audible")).toBeInTheDocument();
  });

  it("filters by account, in the URL", async () => {
    const api = page();
    const { router } = renderApp("/activity?run=145");
    const select = await screen.findByRole("combobox", { name: "Account" });

    await userEvent.selectOptions(select, "Alex (US)");
    expect(router.state.location.search).toBe("?account=2");
    await waitFor(() =>
      expect(
        api.calls.some(
          (call) => call.path === "/api/sync/runs" && new URL(call.url).searchParams.get("account_id") === "2",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("3 runs for Alex (US)")).toBeInTheDocument();
  });

  it("shows the log at a chosen level and follows it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = page();
    renderApp("/activity");
    const log = await screen.findByRole("log", { name: "Log lines" });
    await within(log).findByText("Starting scheduled run");
    expect(within(log).getByText("WARNING")).toBeInTheDocument();

    const reads = () => api.calls.filter((call) => call.path === "/api/logs");
    const before = reads().length;
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await waitFor(() => expect(reads().length).toBeGreaterThan(before));

    fireEvent.click(screen.getByRole("button", { name: "Warning" }));
    await waitFor(() => expect(new URL(reads().at(-1)?.url ?? "http://x").searchParams.get("level")).toBe("WARNING"));
    expect(screen.getByRole("button", { name: "Warning" })).toHaveAttribute("aria-pressed", "true");

    // Scrolling up to read stops following, and with it the polling
    Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(log, "clientHeight", { configurable: true, value: 200 });
    log.scrollTop = 100;
    fireEvent.scroll(log);
    expect(screen.getByRole("checkbox", { name: "Follow" })).not.toBeChecked();
    const paused = reads().length;
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(reads().length).toBe(paused);

    // Following again jumps to the newest line
    fireEvent.click(screen.getByRole("checkbox", { name: "Follow" }));
    expect(log.scrollTop).toBe(1000);
  });

  it("does not call the run in flight interrupted before the status has answered", async () => {
    let answer: (response: Response) => void = () => undefined;
    page({
      "GET /api/status": () => new Promise<Response>((resolve) => (answer = resolve)),
      "GET /api/sync/runs": ok({ items: [run({ id: 150, outcome: "running", finished_at: null })], total: 1 }),
    });
    renderApp("/activity");

    await screen.findByRole("button", { name: "Books in run 150" });
    expect(within(rowOf(150)).queryByText("Interrupted")).not.toBeInTheDocument();
    expect(within(rowOf(150)).getByText("In progress")).toBeInTheDocument();
    answer(json(200, status({ accounts: [UK] })));
    expect(await screen.findByText("Interrupted")).toBeInTheDocument();
  });

  it("brings the linked run into view once Load more has loaded it", async () => {
    const all = Array.from({ length: 25 }, (_, i) => run({ id: 200 - i }));
    page({
      "GET /api/sync/runs": (request: Request) => {
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        return json(200, { items: all.slice(offset, offset + 20), total: all.length });
      },
      "GET /api/sync/runs/178/books": ok({ run_id: 178, recorded: true, downloaded: [], failed: [], refused: [] }),
    });
    const scrolled: string[] = [];
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this.id);
    };
    renderApp("/activity?run=178");

    await screen.findByText("1–20 of 25");
    expect(scrolled).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(scrolled).toEqual(["run-178"]));
    // @ts-expect-error jsdom has none; put it back as it was
    delete Element.prototype.scrollIntoView;
  });

  it("shows and clears a filter on an account that has gone", async () => {
    const { router } = renderFiltered();
    const select = await screen.findByRole("combobox", { name: "Account" });
    expect(await within(select).findByRole("option", { name: "Removed account" })).toBeInTheDocument();
    expect(select).toHaveValue("9");
    expect(await screen.findByText("0 runs for a removed account")).toBeInTheDocument();

    await userEvent.selectOptions(select, "All accounts");
    expect(router.state.location.search).toBe("");
  });

  it("during a run, reads only the run's books again, not the whole history", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let done = 0;
    const api = page({
      "GET /api/status": () =>
        json(
          200,
          status({
            accounts: [UK],
            scheduler: { enabled: true, interval_minutes: 360, next_run_at: null, running: true },
            current_run: {
              run_id: 150,
              started_at: "2026-11-02T14:00:00+00:00",
              stage: "downloading",
              account: null,
              book: { id: 100 + done, asin: "B0", title: `Book ${done}` },
              full_refresh: false,
              books_done: done,
              books_total: 10,
              transfer: null,
            },
          }),
        ),
      "GET /api/sync/runs": ok({ items: [run({ id: 150, outcome: "running", finished_at: null })], total: 1 }),
      "GET /api/sync/runs/150/books": ok({ run_id: 150, recorded: true, downloaded: [], failed: [], refused: [] }),
    });
    renderApp("/activity?run=150");
    await screen.findByRole("heading", { name: "Downloaded (0)" });
    const count = (path: string) => api.calls.filter((call) => call.path === path).length;
    // A cold load during a run reads the history once
    expect(count("/api/sync/runs")).toBe(1);
    const books = count("/api/sync/runs/150/books");

    done = 1;
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await waitFor(() => expect(count("/api/sync/runs/150/books")).toBeGreaterThan(books));
    expect(count("/api/sync/runs")).toBe(1);
  });

  it("says when there are no runs yet", async () => {
    page({ "GET /api/sync/runs": ok({ items: [], total: 0 }) });
    renderApp("/activity");
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
  });
});

function renderPage() {
  page();
  return renderApp("/activity");
}

function renderFiltered() {
  // One account left, so without the filter there would be no select at all
  page({ "GET /api/status": ok(status({ accounts: [UK] })), "GET /api/sync/runs": ok({ items: [], total: 0 }) });
  return renderApp("/activity?account=9");
}
