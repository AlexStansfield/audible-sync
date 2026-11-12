/**
 * Search's state lives in the URL, so a search can be bookmarked and survives a reload
 * (DESIGN.md, Search): the box as typed, the account, the category and its child, the
 * sort, the page, a chosen series and the book open in the panel.
 */
import type { CatalogSort } from "../../catalog/queries";
import { positiveId } from "../../lib/ids";

export type SearchQuery = {
  q: string;
  accountId: number | null;
  category: string | null;
  subcategory: string | null;
  sort: CatalogSort;
  page: number;
  /** A series picked from several that matched a `series:` */
  series: string | null;
  /** The book open in the panel */
  book: string | null;
};

export const SORTS: readonly { value: CatalogSort; label: string }[] = [
  { value: "relevance", label: "Relevance" },
  { value: "bestsellers", label: "Bestsellers" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "title", label: "Title" },
];

/** `GET /api/catalog/search` refuses a longer term (422) */
export const MAX_SEARCH = 200;

export const DEFAULT_QUERY: SearchQuery = {
  q: "",
  accountId: null,
  category: null,
  subcategory: null,
  sort: "relevance",
  page: 1,
  series: null,
  book: null,
};

const ASIN = /^[A-Za-z0-9]{10}$/;
const CATEGORY = /^[0-9]{1,20}$/;

export function parse(params: URLSearchParams): SearchQuery {
  const pick = (name: string, pattern: RegExp) => {
    const value = params.get(name);
    return value && pattern.test(value) ? value : null;
  };
  const category = pick("category", CATEGORY);
  return {
    q: (params.get("q") ?? "").slice(0, MAX_SEARCH).trim(),
    accountId: positiveId(params.get("account")),
    category,
    // A child means nothing without its parent
    subcategory: category ? pick("sub", CATEGORY) : null,
    sort: SORTS.find((option) => option.value === params.get("sort"))?.value ?? DEFAULT_QUERY.sort,
    page: positiveId(params.get("page")) ?? 1,
    series: pick("series", ASIN),
    book: pick("book", ASIN),
  };
}

export function serialise(query: SearchQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.accountId !== null) params.set("account", String(query.accountId));
  if (query.category) params.set("category", query.category);
  if (query.category && query.subcategory) params.set("sub", query.subcategory);
  if (query.sort !== DEFAULT_QUERY.sort) params.set("sort", query.sort);
  if (query.page !== 1) params.set("page", String(query.page));
  if (query.series) params.set("series", query.series);
  if (query.book) params.set("book", query.book);
  return params;
}

/** "1–20 of 64". */
export function rangeText(page: number, pageSize: number, total: number): string {
  if (total === 0) return "0 results";
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  return `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()}`;
}
