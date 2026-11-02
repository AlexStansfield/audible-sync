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

/** What a finished run changes, refetched when one ends. */
export const AFTER_RUN = [["stats"], ["books"], ["runs"], ["queue"], ["accounts"]] as const;

export const RUNNING_INTERVAL = 2_000;
export const IDLE_INTERVAL = 30_000;

/** Every 2 s while a run is in flight, every 30 s otherwise. */
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

export function refreshAfterRun(client: QueryClient): Promise<unknown> {
  return Promise.all(AFTER_RUN.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })));
}

/** Refetches the stats, library, runs, queue and accounts when a run in flight ends. */
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
 * refetches `queryKeys` (the end of the run refetches everything anyway).
 */
export function useRefreshDuringRun(status: Status | undefined, queryKeys: readonly (readonly unknown[])[]): void {
  const client = useQueryClient();
  const run = status?.current_run;
  const done = run ? `${run.books_done}:${run.book?.id ?? ""}` : undefined;
  const seen = useRef(done);
  // `queryKeys` is a module constant at each call site, so it never re-runs this by itself
  useEffect(() => {
    if (done !== undefined && seen.current !== undefined && done !== seen.current) {
      for (const queryKey of queryKeys) {
        void client.invalidateQueries({ queryKey: [...queryKey] });
      }
    }
    seen.current = done;
  }, [done, client, queryKeys]);
}
