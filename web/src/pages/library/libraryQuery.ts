/**
 * The Library's filters live in the URL query string, so a filtered view can be
 * bookmarked and survives a reload (DESIGN.md, Library). `parse` reads them with the
 * defaults filled in and anything unreadable dropped; `serialise` writes back only what
 * differs from the defaults, so the plain Library is plain `/library`.
 */

import type { components, operations } from "../../api/schema";
import type { BookStatus } from "../../lib/format";
import { positiveId } from "../../lib/ids";

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
/** How long typing pauses before the search runs */
export const SEARCH_DELAY = 300;
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

/** The filters as `POST /api/books/bulk` takes them, for "select all matching". */
export function bulkFilter(query: LibraryQuery): components["schemas"]["BookFilter"] {
  return {
    account_id: query.accountId,
    status: query.status,
    monitored: query.monitored,
    q: query.q || null,
  };
}

/** `GET /api/books`' query for this view. */
export function listParams(query: LibraryQuery) {
  return {
    q: query.q || undefined,
    account_id: query.accountId ?? undefined,
    status: query.status ?? undefined,
    monitored: query.monitored ?? undefined,
    sort: query.sort,
    order: query.order,
    page: query.page,
    page_size: PAGE_SIZE,
  };
}

/**
 * The page buttons: the first, the last, and the current one with a neighbour each
 * side, `null` marking a gap - `1 2 3 … 15`, `1 … 6 7 8 … 15`.
 */
export function pageList(current: number, pages: number): (number | null)[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const shown = new Set([1, pages, current - 1, current, current + 1]);
  if (current <= 3) for (const page of [2, 3, 4]) shown.add(page);
  if (current >= pages - 2) for (const page of [pages - 3, pages - 2, pages - 1]) shown.add(page);
  const sorted = [...shown].filter((page) => page >= 1 && page <= pages).sort((a, b) => a - b);
  const list: (number | null)[] = [];
  for (const page of sorted) {
    const last = list[list.length - 1];
    if (typeof last === "number" && page - last > 1) list.push(null);
    list.push(page);
  }
  return list;
}

/** "1–24 of 354". */
export function rangeText(page: number, pageSize: number, total: number): string {
  if (total === 0) return "0 books";
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  return `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()}`;
}
