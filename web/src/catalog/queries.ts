/**
 * What Search and Wishlist read and change. Every catalogue answer is under
 * `["catalog"]` and the wishlist under `["wishlist"]`: adding or removing a book
 * refetches both, since a book's markers are worked out per answer by the service.
 */
import { keepPreviousData, type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, unwrap } from "../api/client";
import type { components } from "../api/schema";
import type { Parsed } from "../lib/prefixSearch";

export type CatalogSort = "relevance" | "bestsellers" | "newest" | "oldest" | "title";
export type WishlistSort = "date_added" | "title" | "release_date";
export type CatalogBookDetail = components["schemas"]["CatalogBookDetail"];

export const SEARCH_PAGE_SIZE = 20;
export const WISHLIST_PAGE_SIZE = 50;

/**
 * How many results Audible pages through, by sort (the service's `reachable_results`):
 * a page beyond is a 422, so the pager never offers one.
 */
export function reach(sort: CatalogSort): number {
  return sort === "bestsellers" ? 5000 : 1000;
}

/** What a change to a wishlist moves: the list, the count, and every book's markers. */
const CHANGED_BY_WISHLIST = [["catalog"], ["wishlist"]] as const;

/** Refetches what adding, removing or reading a wishlist moves. */
function refreshWishlist(client: QueryClient): Promise<unknown> {
  return Promise.all(CHANGED_BY_WISHLIST.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })));
}

export type SearchParams = {
  accountId: number | null;
  parsed: Parsed;
  categoryId: string | null;
  sort: CatalogSort;
  page: number;
};

/** Whether a search has anything to ask: keywords, a field or a category. */
export function hasTerms(parsed: Parsed, categoryId: string | null): boolean {
  return Boolean(parsed.keywords || parsed.author || parsed.narrator || parsed.title || categoryId);
}

export function useCatalogSearch(params: SearchParams, enabled: boolean) {
  const { parsed } = params;
  return useQuery({
    queryKey: ["catalog", "search", params],
    queryFn: () =>
      unwrap(
        api.GET("/api/catalog/search", {
          params: {
            query: {
              account_id: params.accountId ?? undefined,
              keywords: parsed.keywords || undefined,
              author: parsed.author,
              narrator: parsed.narrator,
              title: parsed.title,
              category_id: params.categoryId ?? undefined,
              sort: params.sort,
              page: params.page,
              page_size: SEARCH_PAGE_SIZE,
            },
          },
        }),
      ),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useCategories(accountId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", "categories", accountId],
    queryFn: () =>
      unwrap(api.GET("/api/catalog/categories", { params: { query: { account_id: accountId ?? undefined } } })),
    enabled,
    staleTime: 60 * 60_000,
    retry: false,
  });
}

export function useMarketplaces() {
  return useQuery({
    queryKey: ["marketplaces"],
    queryFn: () => unwrap(api.GET("/api/marketplaces")),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useSeriesMatches(
  name: string | undefined,
  author: string | undefined,
  accountId: number | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["catalog", "series-matches", name, author, accountId],
    queryFn: () =>
      unwrap(
        api.GET("/api/catalog/series", {
          params: { query: { name: name ?? "", author, account_id: accountId ?? undefined } },
        }),
      ),
    enabled: enabled && Boolean(name),
    retry: false,
  });
}

export function useSeries(asin: string | null, accountId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", "series", asin, accountId],
    queryFn: () =>
      unwrap(
        api.GET("/api/catalog/series/{asin}", {
          params: { path: { asin: asin ?? "" }, query: { account_id: accountId ?? undefined } },
        }),
      ),
    enabled: enabled && Boolean(asin),
    retry: false,
  });
}

export function useCatalogBook(asin: string | null, accountId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["catalog", "book", asin, accountId],
    queryFn: () =>
      unwrap(
        api.GET("/api/catalog/books/{asin}", {
          params: { path: { asin: asin ?? "" }, query: { account_id: accountId ?? undefined } },
        }),
      ),
    enabled: enabled && Boolean(asin),
    retry: false,
  });
}

export function useWishlist(query: { accountId: number | null; sort: WishlistSort; page: number }) {
  return useQuery({
    queryKey: ["wishlist", "list", query],
    queryFn: () =>
      unwrap(
        api.GET("/api/wishlist", {
          params: {
            query: {
              account_id: query.accountId ?? undefined,
              sort: query.sort,
              page: query.page,
              page_size: WISHLIST_PAGE_SIZE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

/** The number beside Wishlist in the sidebar: one row asked for, the total read. */
export function useWishlistCount() {
  return useQuery({
    queryKey: ["wishlist", "count"],
    queryFn: () => unwrap(api.GET("/api/wishlist", { params: { query: { page_size: 1 } } })),
    select: (list) => list.total,
  });
}

/**
 * Adding a book to a wishlist, and taking one off. The busy state lasts until the
 * refetch has the new markers, so a marker changes only once the service has said so,
 * and a failure refetches too: Audible may have made the change before the error.
 */
export function useWishlistChange() {
  const client = useQueryClient();
  const refresh = () => refreshWishlist(client);
  const add = useMutation({
    mutationFn: ({ accountId, asin }: { accountId: number; asin: string }) =>
      unwrap(api.POST("/api/wishlist", { body: { account_id: accountId, asin } })),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: async ({ accountId, asin }: { accountId: number; asin: string }) => {
      try {
        return await unwrap(
          api.DELETE("/api/wishlist/{account_id}/{asin}", { params: { path: { account_id: accountId, asin } } }),
        );
      } catch (error) {
        // Not on the list as last read (a sync saw it go): Audible was told all the same,
        // so it is off the wishlist either way
        if (error instanceof ApiError && error.status === 404) return { status: "removed" };
        throw error;
      }
    },
    onSettled: refresh,
  });
  return { add, remove };
}

/** Refresh from Audible: every signed-in account's wishlist read again. */
export function useWishlistRefresh() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/wishlist/refresh", { body: {} })),
    onSuccess: () => refreshWishlist(client),
  });
}
