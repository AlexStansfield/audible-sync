import { describe, expect, it } from "vitest";
import { counts } from "../../test/fixtures";
import {
  bulkFilter,
  chipCounts,
  DEFAULT_QUERY,
  listParams,
  pageList,
  parse,
  rangeText,
  serialise,
} from "./libraryQuery";
import { liveBook } from "./live";
import { EMPTY, isSelected, pageSelected, selectedCount, selectionReducer } from "./selection";
import { bulkResultText, librarySubtitle, noResultsText, selectionLabel } from "./text";

const read = (search: string) => parse(new URLSearchParams(search));

describe("the Library's URL", () => {
  it("reads the defaults from an empty query and writes them back as nothing", () => {
    expect(read("")).toEqual(DEFAULT_QUERY);
    expect(serialise(DEFAULT_QUERY).toString()).toBe("");
  });

  it("reads the links the Dashboard makes", () => {
    expect(read("status=waiting_download&monitored=true")).toMatchObject({
      status: "waiting_download",
      monitored: true,
    });
    expect(read("monitored=false").monitored).toBe(false);
  });

  it("round-trips every filter", () => {
    const query = {
      q: "red rising",
      accountId: 2,
      status: "failed" as const,
      monitored: false,
      sort: "title" as const,
      order: "desc" as const,
      page: 3,
    };
    expect(parse(serialise(query))).toEqual(query);
    expect(serialise(query).toString()).toBe(
      "q=red+rising&account=2&status=failed&monitored=false&sort=title&order=desc&page=3",
    );
  });

  it("gives each sort its natural order and leaves it out of the URL", () => {
    expect(read("sort=title").order).toBe("asc");
    expect(read("sort=release_date").order).toBe("desc");
    expect(serialise(read("sort=author")).toString()).toBe("sort=author");
  });

  it("cuts a search to what the API takes", () => {
    expect(read(`q=${"a".repeat(250)}`).q).toHaveLength(200);
  });

  it("drops what it cannot read rather than sending it on", () => {
    expect(read("status=lost&monitored=maybe&account=x&page=-2&sort=size&order=up&q=%20%20")).toEqual(DEFAULT_QUERY);
    expect(read("page=0").page).toBe(1);
    expect(read("page=2.5").page).toBe(1);
  });

  it("builds the listing request and the bulk filter from the same query", () => {
    const query = read("q=dune&status=downloaded&page=2");
    expect(listParams(query)).toEqual({
      q: "dune",
      account_id: undefined,
      status: "downloaded",
      monitored: undefined,
      sort: "date_added",
      order: "desc",
      page: 2,
      page_size: 24,
    });
    expect(bulkFilter(query)).toEqual({ account_id: null, status: "downloaded", monitored: null, q: "dune" });
  });
});

describe("the pager", () => {
  it("lists every page when there are few", () => {
    expect(pageList(1, 1)).toEqual([1]);
    expect(pageList(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("keeps the ends and the pages around the current one, with gaps between", () => {
    expect(pageList(1, 15)).toEqual([1, 2, 3, 4, null, 15]);
    expect(pageList(7, 15)).toEqual([1, null, 6, 7, 8, null, 15]);
    expect(pageList(15, 15)).toEqual([1, null, 12, 13, 14, 15]);
    expect(pageList(3, 15)).toEqual([1, 2, 3, 4, null, 15]);
  });

  it("words the range shown", () => {
    expect(rangeText(1, 24, 354)).toBe("1–24 of 354");
    expect(rangeText(15, 24, 354)).toBe("337–354 of 354");
    expect(rangeText(1, 24, 0)).toBe("0 books");
  });
});

describe("the selection", () => {
  it("toggles books one at a time, and keeps them across pages", () => {
    let state = selectionReducer(EMPTY, { type: "toggle", id: 1, pageIds: [1, 2] });
    state = selectionReducer(state, { type: "toggle", id: 2, pageIds: [1, 2] });
    state = selectionReducer(state, { type: "page", ids: [3, 4], on: true });
    expect(selectedCount(state, 50)).toBe(4);
    state = selectionReducer(state, { type: "toggle", id: 1, pageIds: [1, 2] });
    expect(isSelected(state, 1)).toBe(false);
    expect(pageSelected(state, [3, 4])).toBe(true);
    state = selectionReducer(state, { type: "page", ids: [3, 4], on: false });
    expect(selectedCount(state, 50)).toBe(1);
  });

  it("counts everything matching as what matches now", () => {
    const all = selectionReducer(EMPTY, { type: "all" });
    expect(selectedCount(all, 354)).toBe(354);
    // A run moved books into the filter since: the action, sent as the filter, reaches them
    expect(selectedCount(all, 359)).toBe(359);
    expect(isSelected(all, 99)).toBe(true);
  });

  it("ends with the filters, books picked by id too", () => {
    expect(selectionReducer(selectionReducer(EMPTY, { type: "all" }), { type: "filters" })).toBe(EMPTY);
    const some = selectionReducer(EMPTY, { type: "toggle", id: 5, pageIds: [5] });
    expect(selectionReducer(some, { type: "filters" })).toBe(EMPTY);
  });

  it("narrows 'all matching' to the rest of the page when one book is unticked", () => {
    const all = selectionReducer(EMPTY, { type: "all" });
    const rest = selectionReducer(all, { type: "toggle", id: 2, pageIds: [1, 2, 3] });
    expect(rest).toEqual({ all: false, ids: new Set([1, 3]) });
    expect(selectionReducer(all, { type: "page", ids: [1, 2, 3], on: false })).toBe(EMPTY);
    expect(selectionReducer(all, { type: "clear" })).toBe(EMPTY);
  });

  it("labels the count", () => {
    expect(selectionLabel(6, false)).toBe("6 selected");
    expect(selectionLabel(354, true)).toBe("All 354 matching books selected");
  });
});

describe("the Library's wording", () => {
  it("says how big the library is", () => {
    expect(librarySubtitle(354, 2)).toBe("354 books across 2 accounts");
    expect(librarySubtitle(306, 1)).toBe("306 books");
    expect(librarySubtitle(0, 1)).toBe("No books yet");
  });

  it("restates the filters that matched nothing", () => {
    expect(noResultsText(read("q=zelazny&status=failed"))).toBe('Nothing matches "zelazny" in Failed.');
    expect(noResultsText(read("monitored=false"))).toBe("No books among the unmonitored books.");
    expect(noResultsText(read("q=x"), "Alex (US)")).toBe('Nothing matches "x" in Alex (US).');
    expect(noResultsText(read("q=author%3AMark%20Lawrence%20series%3ABroken%20Empire&status=failed"))).toBe(
      "Nothing matches author Mark Lawrence and series “Broken Empire” in Failed.",
    );
  });

  it("words a monitor the way the design does, counting what was queued", () => {
    const result = { updated: 6, queued: 4, skipped: [{ id: 9, reason: "busy" as const }] };
    expect(bulkResultText("monitor", result)).toEqual({
      headline: "6 books monitored, 4 of them queued for download.",
      detail: "1 book skipped because a run is downloading it.",
    });
    expect(bulkResultText("monitor", { updated: 2, queued: 2, skipped: [] }).headline).toBe(
      "2 books monitored and queued for download.",
    );
    expect(bulkResultText("monitor", { updated: 1, queued: 0, skipped: [] }).headline).toBe("1 book monitored.");
  });

  it("words the other actions and groups the skips by reason", () => {
    expect(bulkResultText("unmonitor", { updated: 3, queued: 0, skipped: [] })).toEqual({
      headline: "3 books unmonitored.",
      detail: "",
    });
    expect(
      bulkResultText("retry", {
        updated: 2,
        queued: 2,
        skipped: [
          { id: 1, reason: "downloaded" },
          { id: 2, reason: "downloaded" },
          { id: 3, reason: "withdrawn" },
        ],
      }),
    ).toEqual({
      headline: "2 books queued again.",
      detail:
        "2 books skipped because they are already downloaded. 1 book skipped because Audible withdrew the title; it comes back on its own.",
    });
    expect(
      bulkResultText("delete_files", {
        updated: 4,
        queued: 0,
        skipped: [
          { id: 5, reason: "no_files" },
          { id: 6, reason: "error" },
        ],
      }),
    ).toEqual({
      headline: "Deleted the files for 4 books.",
      detail: "1 book skipped with no files to delete. 1 book could not be changed; the service log says why.",
    });
    expect(bulkResultText("retry", { updated: 0, queued: 0, skipped: [{ id: 1, reason: "not_found" }] })).toEqual({
      headline: "No books changed.",
      detail: "1 book skipped because it is no longer in the library.",
    });
  });
});

describe("the book a run is downloading", () => {
  const snapshot = (transfer: { desc: string | null; bytes: number; total: number | null } | null) => ({
    run_id: 9,
    started_at: "2026-11-02T14:00:00+00:00",
    stage: "downloading" as const,
    account: null,
    book: { id: 7, asin: "B0", title: "Dune" },
    full_refresh: false,
    books_done: 0,
    books_total: 10,
    transfer,
  });

  it("follows the audio's download", () => {
    expect(liveBook(snapshot({ desc: "Dune", bytes: 25, total: 100 }))).toEqual({ id: 7, fraction: 0.25 });
  });

  it("slides rather than starting again for the PDF, the cover and the decryption", () => {
    expect(liveBook(snapshot({ desc: "PDF", bytes: 1, total: 100 }))?.fraction).toBeNull();
    expect(liveBook(snapshot({ desc: "Cover", bytes: 1, total: 100 }))?.fraction).toBeNull();
    expect(liveBook(snapshot(null))?.fraction).toBeNull();
    expect(liveBook(snapshot({ desc: "Dune", bytes: 5, total: null }))?.fraction).toBeNull();
  });

  it("is nothing between books and runs", () => {
    expect(liveBook(null)).toBeNull();
    expect(liveBook({ ...snapshot(null), book: null })).toBeNull();
  });
});

describe("chipCounts", () => {
  const books = counts({ downloaded: 10, waiting_download: 6, failed: 2 }, 0, {
    monitored_by_status: { waiting_download: 4, downloading: 0, downloaded: 9, unavailable: 0, failed: 2 },
  });

  it("counts every book without the monitored filter", () => {
    expect(chipCounts(books, null)).toEqual({ total: 18, byStatus: books.by_status });
  });

  it("counts the monitored books, or the rest", () => {
    expect(chipCounts(books, true)).toEqual({
      total: 15,
      byStatus: { waiting_download: 4, downloading: 0, downloaded: 9, unavailable: 0, failed: 2 },
    });
    expect(chipCounts(books, false)).toEqual({
      total: 3,
      byStatus: { waiting_download: 2, downloading: 0, downloaded: 1, unavailable: 0, failed: 0 },
    });
  });
});
