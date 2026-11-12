/**
 * The Library's filters live in the URL query string, so a filtered view can be
 * bookmarked and survives a reload (DESIGN.md, Library). `parse` reads them with the
 * defaults filled in and anything unreadable dropped; `serialise` writes back only what
 * differs from the defaults, so the plain Library is plain `/library`.
 */

import type { components, operations } from "../../api/schema";
import type { BookStatus } from "../../lib/format";
import { positiveId } from "../../lib/ids";
import { parseSearch } from "../../lib/prefixSearch";

/** `GET /api/books`' `sort` (the API's `Literal`, kept in step with `BOOK_SORT_COLUMNS`) */
export type Sort = NonNullable<NonNullable<operations["get_books_api_books_get"]["parameters"]["query"]>["sort"]>;
export type Order = "asc" | "desc";

export type LibraryQuery = {
  q: string;
  accountId: number | null;
  status: BookStatus | null;
  /** `true` monitored only, `false` unmonitored only, `null` both */
  monitored: boolean | null;
  sort: Sort;
  order: Order;
  page: number;
};

export const STATUSES: readonly BookStatus[] = [
  "downloaded",
  "waiting_download",
  "downloading",
  "unavailable",
  "failed",
];

/** The sorts the toolbar offers, each with the order it reads naturally in. */
export const SORTS: readonly { value: Sort; label: string; order: Order }[] = [
  { value: "date_added", label: "Date added", order: "desc" },
  { value: "title", label: "Title", order: "asc" },
  { value: "author", label: "Author", order: "asc" },
  { value: "release_date", label: "Release date", order: "desc" },
  { value: "downloaded_at", label: "Downloaded", order: "desc" },
];

export const PAGE_SIZE = 24;
/** `GET /api/books` refuses a longer `q` (422) */
export const MAX_SEARCH = 200;
/** Where the grid/table choice is kept */
export const VIEW_KEY = "audible-sync.library-view";

export const DEFAULT_QUERY: LibraryQuery = {
  q: "",
  accountId: null,
  status: null,
  monitored: null,
  sort: "date_added",
  order: "desc",
  page: 1,
};

export function defaultOrder(sort: Sort): Order {
  return SORTS.find((option) => option.value === sort)?.order ?? "desc";
}

export function parse(params: URLSearchParams): LibraryQuery {
  const status = params.get("status");
  const sort = SORTS.find((option) => option.value === params.get("sort"))?.value ?? DEFAULT_QUERY.sort;
  const order = params.get("order");
  const monitored = params.get("monitored");
  return {
    q: (params.get("q") ?? "").slice(0, MAX_SEARCH).trim(),
    accountId: positiveId(params.get("account")),
    status: STATUSES.includes(status as BookStatus) ? (status as BookStatus) : null,
    monitored: monitored === "true" ? true : monitored === "false" ? false : null,
    sort,
    order: order === "asc" || order === "desc" ? order : defaultOrder(sort),
    page: positiveId(params.get("page")) ?? 1,
  };
}

export function serialise(query: LibraryQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.accountId !== null) params.set("account", String(query.accountId));
  if (query.status) params.set("status", query.status);
  if (query.monitored !== null) params.set("monitored", String(query.monitored));
  if (query.sort !== DEFAULT_QUERY.sort) params.set("sort", query.sort);
  if (query.order !== defaultOrder(query.sort)) params.set("order", query.order);
  if (query.page !== 1) params.set("page", String(query.page));
  return params;
}

/**
 * The search box read into the API's fields: plain words are `q` (title, author or
 * series), each prefix its own field (`title`, `author`, `narrator`, `series`).
 */
function searchFields(q: string) {
  const { keywords, ...fields } = parseSearch(q);
  return { q: keywords || null, ...fields };
}

/** The filters as `POST /api/books/bulk` takes them, for "select all matching". */
export function bulkFilter(query: LibraryQuery): components["schemas"]["BookFilter"] {
  return {
    account_id: query.accountId,
    status: query.status,
    monitored: query.monitored,
    ...searchFields(query.q),
  };
}

/** `GET /api/books`' query for this view. */
export function listParams(query: LibraryQuery) {
  const { q, ...fields } = searchFields(query.q);
  return {
    q: q ?? undefined,
    ...fields,
    account_id: query.accountId ?? undefined,
    status: query.status ?? undefined,
    monitored: query.monitored ?? undefined,
    sort: query.sort,
    order: query.order,
    page: query.page,
    page_size: PAGE_SIZE,
  };
}

/** What the status chips count: the books by status, and all of them. */
export type ChipCounts = { total: number; byStatus: Record<string, number> };

/**
 * The status chips' counts for the `monitored` filter, from `GET /api/stats`' books:
 * every book, the monitored ones (`monitored_by_status`), or the rest - so a chip says
 * what choosing it lists. The search is not counted: the chips count the library.
 */
export function chipCounts(counts: components["schemas"]["BookCounts"], monitored: boolean | null): ChipCounts {
  if (monitored === null) return { total: counts.total, byStatus: counts.by_status };
  const byStatus = Object.fromEntries(
    Object.entries(counts.by_status).map(([status, count]) => {
      const wanted = counts.monitored_by_status[status] ?? 0;
      return [status, monitored ? wanted : count - wanted];
    }),
  );
  return { total: monitored ? counts.monitored : counts.unmonitored, byStatus };
}
