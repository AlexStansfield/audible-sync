/**
 * The Library's wording, kept out of the components so it can be tested on its own.
 */
import type { components } from "../../api/schema";
import { BOOK_STATUS, plural } from "../../lib/format";
import type { LibraryQuery } from "./libraryQuery";

type BulkAction = components["schemas"]["BulkAction"];
type BulkResult = components["schemas"]["BulkResultOut"];
type SkipReason = components["schemas"]["SkipReason"];

/** Under the title: `354 books across 2 accounts`, `306 books`, `No books yet`. */
export function librarySubtitle(total: number, accounts: number): string {
  if (total === 0) return "No books yet";
  return accounts > 1 ? `${plural(total, "book")} across ${accounts} accounts` : plural(total, "book");
}

/** The no-results card: `Nothing matches "zelazny" in Failed.` */
export function noResultsText(query: LibraryQuery, accountName?: string): string {
  const parts = [
    query.status && `in ${BOOK_STATUS[query.status].label}`,
    query.monitored === false && "among the unmonitored books",
    query.monitored === true && "among the monitored books",
    accountName && `in ${accountName}`,
  ].filter(Boolean);
  const what = query.q ? `Nothing matches "${query.q}"` : "No books";
  return `${[what, ...parts].join(" ")}.`;
}

const DONE: Record<BulkAction, string> = {
  monitor: "monitored",
  unmonitor: "unmonitored",
  retry: "queued again",
  delete_files: "had their files deleted",
};

/** Why books were skipped, by reason: `1 skipped because a run is downloading it.` */
const SKIPPED: Record<SkipReason, (n: number) => string> = {
  busy: (n) => `${plural(n, "book")} skipped because a run is downloading ${n === 1 ? "it" : "them"}.`,
  downloaded: (n) => `${plural(n, "book")} skipped because ${n === 1 ? "it is" : "they are"} already downloaded.`,
  withdrawn: (n) =>
    `${plural(n, "book")} skipped because Audible withdrew ${n === 1 ? "the title; it comes" : "the titles; they come"} back on ${n === 1 ? "its" : "their"} own.`,
  no_files: (n) => `${plural(n, "book")} skipped with no files to delete.`,
  not_found: (n) => `${plural(n, "book")} skipped because ${n === 1 ? "it is" : "they are"} no longer in the library.`,
  error: (n) => `${plural(n, "book")} could not be changed; the service log says why.`,
};

const SKIP_ORDER: readonly SkipReason[] = ["busy", "downloaded", "withdrawn", "no_files", "not_found", "error"];

/**
 * The result banner after a bulk action: what changed, then what was skipped and why.
 * Monitor says how many were queued, since failed and unavailable books keep their
 * status until retried.
 */
export function bulkResultText(action: BulkAction, result: BulkResult): { headline: string; detail: string } {
  const { updated, queued } = result;
  let headline: string;
  if (updated === 0) {
    headline = "No books changed.";
  } else if (action === "monitor") {
    headline =
      queued === 0
        ? `${plural(updated, "book")} monitored.`
        : queued === updated
          ? `${plural(updated, "book")} monitored and queued for download.`
          : `${plural(updated, "book")} monitored, ${queued.toLocaleString()} of them queued for download.`;
  } else if (action === "delete_files") {
    headline = `Deleted the files for ${plural(updated, "book")}.`;
  } else {
    headline = `${plural(updated, "book")} ${DONE[action]}.`;
  }
  const counts = new Map<SkipReason, number>();
  for (const skip of result.skipped) counts.set(skip.reason, (counts.get(skip.reason) ?? 0) + 1);
  const detail = SKIP_ORDER.filter((reason) => counts.has(reason))
    .map((reason) => SKIPPED[reason](counts.get(reason) ?? 0))
    .join(" ");
  return { headline, detail };
}

/** The selection bar's count. */
export function selectionLabel(count: number, all: boolean): string {
  return all ? `All ${count.toLocaleString()} matching books selected` : `${count.toLocaleString()} selected`;
}
