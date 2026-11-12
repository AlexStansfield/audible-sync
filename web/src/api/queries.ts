/**
 * The queries more than one screen reads. `GET /api/status` is the one polled query:
 * the sidebar's service state, the dashboard's run card and the library's Downloading
 * badges all read it (DESIGN.md, "Polling").
 */
import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, unwrap } from "./client";
import type { components } from "./schema";

export type Me = components["schemas"]["Me"];
export type Status = components["schemas"]["Status"];

export const keys = {
  me: ["me"] as const,
  health: ["health"] as const,
  status: ["status"] as const,
  bookCount: ["books", "count"] as const,
};

/**
 * What a finished run changes, refetched when one ends. A run reads every wishlist, and
 * what it adds to a library moves the catalogue's "In library" markers.
 */
export const AFTER_RUN = [["stats"], ["books"], ["runs"], ["queue"], ["accounts"], ["wishlist"], ["catalog"]] as const;

/**
 * `GET /api/settings` as one cache entry, `["settings"]`: Settings edits it, Book
 * detail reads `max_attempts` from it. Spread into `useQuery`, with a `select` if need be.
 */
export const SETTINGS_KEY = ["settings"] as const;
export const settingsQuery = { queryKey: SETTINGS_KEY, queryFn: () => unwrap(api.GET("/api/settings")) };

export const RUNNING_INTERVAL = 2_000;
/** Short enough that a run the schedule starts shows within seconds, not half a minute */
export const IDLE_INTERVAL = 10_000;

/** Every 2 s while a run is in flight, every 10 s otherwise. */
export function statusInterval(status: Status | undefined): number {
  return status?.scheduler.running ? RUNNING_INTERVAL : IDLE_INTERVAL;
}

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => unwrap(api.GET("/api/auth/me")), retry: false });
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => unwrap(api.GET("/api/health")),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: () => unwrap(api.GET("/api/status")),
    refetchInterval: (query) => statusInterval(query.state.data),
  });
}

/** The library's size for the sidebar: one row asked for, the total read. */
export function useBookCount() {
  return useQuery({
    queryKey: keys.bookCount,
    queryFn: () => unwrap(api.GET("/api/books", { params: { query: { page_size: 1 } } })),
    select: (list) => list.total,
  });
}

/** One book's query, which Book detail and the Dashboard's run card read. */
export const bookKey = (id: number) => ["books", id] as const;

/** What a change to books moves: every listing and book, the totals, the queue. */
const CHANGED_BY_BOOKS = [["books"], ["stats"], ["queue"]] as const;

/** Refetches what a change to one or more books moves (monitoring, an action, a retry). */
export function refreshAfterBookChange(client: QueryClient): Promise<unknown> {
  return Promise.all(CHANGED_BY_BOOKS.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })));
}

export function refreshAfterRun(client: QueryClient): Promise<unknown> {
  return Promise.all(AFTER_RUN.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })));
}

/** Refetches what a run changes (`AFTER_RUN`) when a run in flight ends. */
export function useRefreshAfterRun(running: boolean | undefined): void {
  const client = useQueryClient();
  const was = useRef(running);
  useEffect(() => {
    if (was.current && running === false) {
      void refreshAfterRun(client);
    }
    if (running !== undefined) {
      was.current = running;
    }
  }, [running, client]);
}

/**
 * While a run downloads, what it changes shows as it goes: each book it takes up -
 * refused licences included, which use no slot and so leave `books_done` where it was -
 * refetches `queryKeys`, and so does that book's download starting. The run names the
 * book a moment *before* it claims the row, so a read at the first moment still finds it
 * waiting; once a transfer is under way the claim has happened and the row says
 * downloading (found live, 2026-11-02: the Downloading filter stayed empty all download).
 * The end of the run refetches everything anyway.
 */
export function useRefreshDuringRun(status: Status | undefined, queryKeys: readonly (readonly unknown[])[]): void {
  const client = useQueryClient();
  const run = status?.current_run;
  const book = run ? `${run.books_done}:${run.book?.id ?? ""}` : undefined;
  // The book whose download has started, once it has
  const transferring = run?.transfer ? book : undefined;
  const seen = useRef(book);
  const started = useRef(transferring);
  // `queryKeys` is a module constant at each call site, so it never re-runs this by itself
  useEffect(() => {
    const nextBook = book !== undefined && seen.current !== undefined && book !== seen.current;
    const firstTransfer = transferring !== undefined && transferring !== started.current;
    if (nextBook || firstTransfer) {
      for (const queryKey of queryKeys) {
        void client.invalidateQueries({ queryKey: [...queryKey] });
      }
    }
    seen.current = book;
    if (transferring !== undefined) started.current = transferring;
  }, [book, transferring, client, queryKeys]);
}
