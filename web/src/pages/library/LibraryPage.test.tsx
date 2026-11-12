import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { fail, fakeApi, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { account, type Book, book, counts } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { VIEW_KEY } from "./libraryQuery";

const UK = account();
const US = account({ id: 2, name: "Alex (US)", country_code: "us" });

const STATS = {
  accounts: [
    {
      account: UK,
      books: counts({ downloaded: 30, waiting_download: 6, failed: 2 }),
      bytes_on_disk: 0,
      last_run: null,
    },
    { account: US, books: counts({ downloaded: 10, unavailable: 2 }), bytes_on_disk: 0, last_run: null },
  ],
  books: counts({ downloaded: 40, waiting_download: 6, unavailable: 2, failed: 2 }, 5),
  bytes_on_disk: 0,
  last_run: null,
  next_run_at: null,
};

const PAGE: Book[] = [
  book({
    id: 1,
    title: "Red Rising",
    status: "downloaded",
    primary_series: { title: "Red Rising Saga", sequence: "1" },
  }),
  book({ id: 2, title: "Golden Son", status: "waiting_download" }),
  book({ id: 3, title: "Dune", authors: ["Frank Herbert"], status: "failed", account_id: 2, monitored: false }),
];

/** The listing: `items` for the page asked for, the sidebar's one-row count otherwise. */
function listing(items: Book[] = PAGE, total = 50): (request: Request) => Response {
  return (request: Request) => {
    const query = new URL(request.url).searchParams;
    if (query.get("page_size") === "1") return Response.json({ items: [], total: 50, page: 1, page_size: 1 });
    return Response.json({ items, total, page: Number(query.get("page") ?? 1), page_size: 24 });
  };
}

function library(extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/status": ok(status({ accounts: [UK, US] })),
      "GET /api/stats": ok(STATS),
      "GET /api/books": listing(),
      ...extra,
    }),
  );
}

/** The listing requests the page made (not the sidebar's count), as query strings. */
function listRequests(api: ReturnType<typeof fakeApi>): URLSearchParams[] {
  return api.calls
    .filter((call) => call.method === "GET" && call.path === "/api/books")
    .map((call) => new URL(call.url).searchParams)
    .filter((query) => query.get("page_size") !== "1");
}

afterEach(() => localStorage.clear());

describe("the Library", () => {
  it("shows the books in a grid, with their status and the filters' counts", async () => {
    library();
    renderApp("/library");

    const books = await screen.findByRole("region", { name: "Books" });
    expect(within(books).getByRole("link", { name: "Red Rising" })).toHaveAttribute("href", "/library/1");
    expect(within(books).getByText("Failed")).toBeInTheDocument();
    expect(await screen.findByText("50 books across 2 accounts")).toBeInTheDocument();

    const statuses = screen.getByRole("group", { name: "Status" });
    expect(within(statuses).getByRole("button", { name: /All\s*50/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(statuses).getByRole("button", { name: /Waiting\s*6/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("navigation", { name: "Pages" })).toHaveTextContent("1–24 of 50");
    expect(screen.getByRole("button", { name: "Retry unavailable (2)" })).toBeInTheDocument();
  });

  it("reads the filters the Dashboard links with", async () => {
    const api = library();
    renderApp("/library?status=waiting_download&monitored=true");

    await screen.findByRole("region", { name: "Books" });
    const query = listRequests(api).at(-1);
    expect(query?.get("status")).toBe("waiting_download");
    expect(query?.get("monitored")).toBe("true");
    expect(screen.getByRole("button", { name: /Waiting/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("checkbox", { name: "Monitored only" })).toBeChecked();
    // The chips count the monitored books alone, as the listing does: 5 of the 6 waiting are not
    const statuses = screen.getByRole("group", { name: "Status" });
    expect(within(statuses).getByRole("button", { name: /Waiting\s*1$/ })).toBeInTheDocument();
    expect(within(statuses).getByRole("button", { name: /All\s*45$/ })).toBeInTheDocument();
  });

  it("counts only the unmonitored books on the chips under Unmonitored only", async () => {
    library();
    renderApp("/library?monitored=false");

    const statuses = await screen.findByRole("group", { name: "Status" });
    await waitFor(() => expect(within(statuses).getByRole("button", { name: /All\s*5$/ })).toBeInTheDocument());
    expect(within(statuses).getByRole("button", { name: /Waiting\s*5$/ })).toBeInTheDocument();
    expect(within(statuses).getByRole("button", { name: /Downloaded\s*0$/ })).toBeInTheDocument();
  });

  it("offers Retry unavailable only for books it would queue", async () => {
    const books = counts({ downloaded: 40, waiting_download: 6, unavailable: 2, failed: 2 }, 5, {
      retryable_unavailable: 0,
    });
    library({ "GET /api/stats": ok({ ...STATS, books }) });
    renderApp("/library");

    const statuses = await screen.findByRole("group", { name: "Status" });
    await waitFor(() => expect(within(statuses).getByRole("button", { name: /Unavailable\s*2$/ })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Retry unavailable/ })).not.toBeInTheDocument();
  });

  it("puts a status, the unmonitored filter and the sort in the URL, back on page 1", async () => {
    const api = library();
    const { router } = renderApp("/library?page=2");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Failed/ }));
    expect(router.state.location.search).toBe("?status=failed");
    await user.click(screen.getByRole("checkbox", { name: "Unmonitored only" }));
    expect(router.state.location.search).toBe("?status=failed&monitored=false");
    await user.selectOptions(screen.getByRole("combobox", { name: "Sort" }), "title");
    expect(router.state.location.search).toBe("?status=failed&monitored=false&sort=title");

    await waitFor(() => {
      const query = listRequests(api).at(-1);
      expect(query?.get("order")).toBe("asc");
      expect(query?.get("page")).toBe("1");
    });
  });

  it("searches once typing pauses", async () => {
    const api = library();
    const { router } = renderApp("/library");
    const user = userEvent.setup();

    await user.type(await screen.findByRole("searchbox", { name: "Search the library" }), "red rising ");
    await waitFor(() => expect(router.state.location.search).toBe("?q=red+rising"));
    await waitFor(() => expect(listRequests(api).at(-1)?.get("q")).toBe("red rising"));
    // One search for what was typed, not one per key
    expect(listRequests(api).filter((query) => query.get("q")).length).toBe(1);
    // The trailing space being typed stays in the box
    expect(screen.getByRole("searchbox")).toHaveValue("red rising ");
  });

  it("filters by account when there are several, with the counts that account's", async () => {
    library();
    const { router } = renderApp("/library");
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByRole("combobox", { name: "Account" }), "2");
    expect(router.state.location.search).toBe("?account=2");
    expect(screen.getByRole("button", { name: /All\s*12/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry unavailable (2)" })).toBeInTheDocument();
  });

  it("pages through the library", async () => {
    const api = library();
    const { router } = renderApp("/library");
    const user = userEvent.setup();

    const pages = await screen.findByRole("navigation", { name: "Pages" });
    expect(within(pages).getByRole("button", { name: "Prev" })).toBeDisabled();
    expect(within(pages).getByRole("button", { name: "Page 1" })).toHaveAttribute("aria-current", "page");
    await user.click(within(pages).getByRole("button", { name: "Next" }));
    expect(router.state.location.search).toBe("?page=2");
    await waitFor(() => expect(listRequests(api).at(-1)?.get("page")).toBe("2"));
    expect(await within(pages).findByText("25–48 of 50")).toBeInTheDocument();
  });

  it("switches to the table and remembers it", async () => {
    library();
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Table view" }));
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Red Rising Saga 1")).toBeInTheDocument();
    expect(within(table).getByText("US")).toBeInTheDocument();
    expect(within(table).getByRole("switch", { name: "Monitor Dune" })).toHaveAttribute("aria-checked", "false");
    expect(localStorage.getItem(VIEW_KEY)).toBe("table");
  });

  it("shows the book a run is downloading as such", async () => {
    library({
      "GET /api/status": ok(
        status({
          accounts: [UK, US],
          scheduler: { enabled: true, interval_minutes: 360, next_run_at: null, running: true },
          current_run: {
            run_id: 9,
            started_at: "2026-11-02T14:00:00+00:00",
            stage: "downloading",
            account: null,
            book: { id: 2, asin: "B0", title: "Golden Son" },
            full_refresh: false,
            books_done: 0,
            books_total: 10,
            transfer: { desc: "Golden Son", bytes: 50, total: 100 },
          },
        }),
      ),
    });
    renderApp("/library");

    const books = await screen.findByRole("region", { name: "Books" });
    expect(await within(books).findByText("Downloading")).toBeInTheDocument();
    expect(within(books).queryByText("Waiting")).not.toBeInTheDocument();
  });
});

describe("monitoring a book", () => {
  it("changes the bookmark at once and sends the change", async () => {
    let release: () => void = () => undefined;
    const api = library({
      "PATCH /api/books/2": () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(Response.json(book({ id: 2, monitored: false })));
        }),
    });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Stop monitoring Golden Son" }));
    // Before the service has answered
    expect(screen.getByRole("button", { name: "Monitor Golden Son" })).toHaveAttribute("aria-pressed", "false");
    expect(api.sent("PATCH /api/books/2")).toEqual([{ monitored: false }]);
    release();
  });

  it("waits while the previous page is still on show", async () => {
    let release: () => void = () => undefined;
    const first = listing();
    library({
      "GET /api/books": (request: Request) => {
        if (new URL(request.url).searchParams.get("page") !== "2") return first(request);
        return new Promise<Response>((resolve) => {
          release = () => resolve(Response.json({ items: [book({ id: 40 })], total: 50, page: 2, page_size: 24 }));
        });
      },
    });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Next" }));
    // Page 1's books, kept on show: a change to one could not be shown on page 2's list
    expect(await screen.findByRole("button", { name: "Stop monitoring Golden Son" })).toBeDisabled();
    release();
    expect(await screen.findByRole("button", { name: "Stop monitoring Book 40" })).toBeEnabled();
  });

  it("holds that book's bookmark, and only that one, until the change is answered", async () => {
    let release: () => void = () => undefined;
    library({
      "PATCH /api/books/2": () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(Response.json(book({ id: 2, monitored: false })));
        }),
    });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Stop monitoring Golden Son" }));
    expect(await screen.findByRole("button", { name: "Monitor Golden Son" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop monitoring Red Rising" })).toBeEnabled();
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /Golden Son/ })).toBeEnabled());
  });

  it("puts it back and says so when the service refuses", async () => {
    library({ "PATCH /api/books/2": fail(500, "database is locked") });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Stop monitoring Golden Son" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not stop monitoring the book");
    expect(screen.getByRole("button", { name: "Stop monitoring Golden Son" })).toHaveAttribute("aria-pressed", "true");
  });
});

describe("bulk actions", () => {
  it("monitors the books picked and words the result", async () => {
    const api = library({
      "POST /api/books/bulk": ok({ updated: 2, queued: 1, skipped: [{ id: 3, reason: "busy" }] }),
    });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    const bar = screen.getByRole("region", { name: "Selection" });
    expect(within(bar).getByRole("button", { name: "Monitor" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Select Dune" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Red Rising" }));
    expect(bar).toHaveTextContent("2 selected");
    await user.click(within(bar).getByRole("button", { name: "Monitor" }));

    expect(api.sent("POST /api/books/bulk")).toEqual([{ action: "monitor", ids: [1, 3] }]);
    expect(await screen.findByText("2 books monitored, 1 of them queued for download.")).toBeInTheDocument();
    expect(screen.getByText("1 book skipped because a run is downloading it.")).toBeInTheDocument();
    // The selection is over
    expect(screen.queryByRole("region", { name: "Selection" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/books monitored/)).not.toBeInTheDocument();
  });

  it("keeps the selection across pages", async () => {
    const api = library({ "POST /api/books/bulk": ok({ updated: 3, queued: 0, skipped: [] }) });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Golden Son" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(listRequests(api).at(-1)?.get("page")).toBe("2"));
    await user.click(screen.getByRole("button", { name: "Select all on this page (3)" }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("3 selected");
  });

  it("drops the selection when the filters change, so no action reaches books out of view", async () => {
    library();
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Red Rising" }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("1 selected");
    await user.click(screen.getByRole("button", { name: /Failed/ }));
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("0 selected");
  });

  it("sends the filters, not the ids, for everything matching", async () => {
    const api = library({ "POST /api/books/bulk": ok({ updated: 6, queued: 6, skipped: [] }) });
    renderApp("/library?status=failed&q=dune");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("button", { name: "Select all 50 matching these filters" }));
    const bar = screen.getByRole("region", { name: "Selection" });
    expect(bar).toHaveTextContent("All 50 matching books selected");
    await user.click(within(bar).getByRole("button", { name: "Retry" }));

    expect(api.sent("POST /api/books/bulk")).toEqual([
      { action: "retry", filter: { account_id: null, status: "failed", monitored: null, q: "dune" } },
    ]);
    expect(await screen.findByText("6 books queued again.")).toBeInTheDocument();
  });

  it("confirms before deleting files, and keeps them when asked to", async () => {
    const api = library({ "POST /api/books/bulk": ok({ updated: 1, queued: 0, skipped: [] }) });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Red Rising" }));
    await user.click(screen.getByRole("button", { name: "Delete files" }));
    const dialog = screen.getByRole("dialog", { name: "Delete the files for 1 book?" });
    await user.click(within(dialog).getByRole("button", { name: "Keep files" }));
    expect(api.sent("POST /api/books/bulk")).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Delete files" }));
    await user.click(screen.getByRole("button", { name: "Delete files for 1 book" }));
    expect(api.sent("POST /api/books/bulk")).toEqual([{ action: "delete_files", ids: [1] }]);
    expect(await screen.findByText("Deleted the files for 1 book.")).toBeInTheDocument();
  });

  it("says so when the request fails, keeping the selection", async () => {
    library({ "POST /api/books/bulk": fail(422, "Give ids or a filter, not both") });
    renderApp("/library");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Red Rising" }));
    await user.click(screen.getByRole("button", { name: "Unmonitor" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Give ids or a filter, not both");
    expect(screen.getByRole("region", { name: "Selection" })).toHaveTextContent("1 selected");
  });
});

describe("the empty states", () => {
  it("points an empty library at Accounts, with no filters", async () => {
    library({
      "GET /api/stats": ok({ ...STATS, accounts: [], books: counts({}) }),
      "GET /api/books": listing([], 0),
    });
    renderApp("/library");

    expect(await screen.findByRole("heading", { name: "Your library is empty" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add an Audible account" })).toHaveAttribute("href", "/accounts");
    expect(screen.queryByRole("region", { name: "Filters" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Select" })).not.toBeInTheDocument();
    expect(screen.getByText("No books yet")).toBeInTheDocument();
  });

  it("restates filters that match nothing and clears them", async () => {
    library({ "GET /api/books": listing([], 0) });
    const { router } = renderApp("/library?q=zelazny&status=failed&sort=title");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "No books match these filters" })).toBeInTheDocument();
    expect(screen.getByText('Nothing matches "zelazny" in Failed.')).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Pages" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    // The sort is how to look, not what to look for: it stays
    expect(router.state.location.search).toBe("?sort=title");
    await waitFor(() => expect(screen.getByRole("searchbox")).toHaveValue(""));
  });

  it("shows the error with Retry when the listing fails", async () => {
    library({ "GET /api/books": fail(500, "database is locked") });
    renderApp("/library");

    // A 5xx is retried once first
    const alert = await screen.findByRole("alert", {}, { timeout: 3000 });
    expect(alert).toHaveTextContent("Could not load the library");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("reads the search box's prefixes into the request, with chips beside the status", async () => {
    const api = library();
    renderApp("/library?q=dark%20author%3AJohn%20Scalzi%20series%3AOld%20Man%27s%20War");

    await screen.findByRole("region", { name: "Books" });
    const query = listRequests(api)[0];
    expect(query?.get("q")).toBe("dark");
    expect(query?.get("author")).toBe("John Scalzi");
    expect(query?.get("series")).toBe("Old Man's War");

    const chips = screen.getByRole("list", { name: "Searching for" });
    expect(within(chips).getByText("John Scalzi")).toBeInTheDocument();
    // "Status" is labelled in sight, as the second of two rows
    expect(screen.getByText("Status")).toBeVisible();

    await userEvent.click(within(chips).getByRole("button", { name: "Remove series Old Man's War" }));
    await waitFor(() => expect(listRequests(api).at(-1)?.get("series")).toBeNull());
    expect(screen.getByRole("searchbox", { name: "Search the library" })).toHaveValue("dark author:John Scalzi");
  });

  it("shows no chips for plain words, which search as they always did", async () => {
    const api = library();
    renderApp("/library?q=dune");

    await screen.findByRole("region", { name: "Books" });
    expect(listRequests(api)[0]?.get("q")).toBe("dune");
    expect(screen.queryByRole("list", { name: "Searching for" })).not.toBeInTheDocument();
  });

  it("sends the prefixes with Select all matching", async () => {
    const api = library({ "POST /api/books/bulk": ok({ updated: 50, queued: 0, skipped: [] }) });
    renderApp("/library?q=author%3AScalzi");

    await screen.findByRole("region", { name: "Books" });
    await userEvent.click(screen.getByRole("button", { name: "Select" }));
    await userEvent.click(screen.getByRole("button", { name: /Select all 50 matching/ }));
    await userEvent.click(screen.getByRole("button", { name: "Monitor" }));

    await waitFor(() => expect(api.sent("POST /api/books/bulk")).toHaveLength(1));
    expect(api.sent("POST /api/books/bulk")[0]).toMatchObject({ filter: { q: null, author: "Scalzi" } });
  });
});
