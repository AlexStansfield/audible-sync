/**
 * What the Dashboard reads. Every key starts with one of `AFTER_RUN`'s, so the end of
 * a run refetches them all.
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";

export const UP_NEXT_ROWS = 4;
export const RECENT_COVERS = 6;
export const RECENT_RUNS = 5;

export function useStats() {
  return useQuery({ queryKey: ["stats"], queryFn: () => unwrap(api.GET("/api/stats")) });
}

export function useUpNext() {
  return useQuery({
    queryKey: ["queue", UP_NEXT_ROWS],
    queryFn: () => unwrap(api.GET("/api/queue", { params: { query: { limit: UP_NEXT_ROWS } } })),
  });
}

export function useRecentlyDownloaded() {
  return useQuery({
    queryKey: ["books", "recent"],
    queryFn: () =>
      unwrap(
        api.GET("/api/books", {
          params: { query: { status: "downloaded", sort: "downloaded_at", order: "desc", page_size: RECENT_COVERS } },
        }),
      ),
  });
}

export function useRecentRuns() {
  return useQuery({
    queryKey: ["runs", "recent"],
    queryFn: () => unwrap(api.GET("/api/sync/runs", { params: { query: { limit: RECENT_RUNS } } })),
  });
}

/** One failed book, for the example error in Needs attention. */
export function useLatestFailure(enabled: boolean) {
  return useQuery({
    queryKey: ["books", "latest-failure"],
    queryFn: () => unwrap(api.GET("/api/books", { params: { query: { status: "failed", page_size: 1 } } })),
    select: (list) => list.items[0] ?? null,
    enabled,
  });
}

/** The book a run is working on, for its author. */
export function useBook(bookId: number | undefined) {
  return useQuery({
    queryKey: ["books", bookId],
    queryFn: () => unwrap(api.GET("/api/books/{book_id}", { params: { path: { book_id: bookId ?? 0 } } })),
    enabled: bookId !== undefined,
  });
}
