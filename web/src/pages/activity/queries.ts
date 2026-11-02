/**
 * What Activity reads. The run history and a run's books start with `runs`, one of
 * `AFTER_RUN`'s keys, so the end of a run refetches them; the log is its own key, polled
 * only while Follow is on.
 */
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, unwrap } from "../../api/client";
import type { Status } from "../../api/queries";
import type { Level } from "./text";

export const RUNS_PAGE = 20;
export const LOG_LINES = 1000;
export const FOLLOW_INTERVAL = 2_000;

/** One run's books. */
export const runBooksKey = (runId: number) => ["runs", runId, "books"] as const;

/**
 * The run history, `RUNS_PAGE` at a time ("Load more" asks for the next offset). A run
 * started since the first page was read pushes the rest down by one, so a row can come
 * back on the next page: the caller drops repeats by id.
 */
export function useRunHistory(accountId: number | null) {
  return useInfiniteQuery({
    queryKey: ["runs", "history", accountId ?? "all"],
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/sync/runs", {
          params: { query: { limit: RUNS_PAGE, offset: pageParam, account_id: accountId ?? undefined } },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return loaded < last.total && last.items.length > 0 ? loaded : undefined;
    },
  });
}

/** One run's books, read when its row is opened. */
export function useRunBooks(runId: number) {
  return useQuery({
    queryKey: runBooksKey(runId),
    queryFn: () => unwrap(api.GET("/api/sync/runs/{run_id}/books", { params: { path: { run_id: runId } } })),
  });
}

/** The newest log lines at or above `level`, every 2 s while following. */
export function useLogs(level: Level, follow: boolean) {
  return useQuery({
    queryKey: ["logs", level],
    queryFn: () => unwrap(api.GET("/api/logs", { params: { query: { limit: LOG_LINES, level } } })),
    refetchInterval: follow ? FOLLOW_INTERVAL : false,
  });
}

/**
 * A run starting shows in the history at once, as a running row. Only a change seen
 * after the first status counts: on a cold load during a run, the first answer is not
 * a run starting, and the history was just read anyway.
 */
export function useRefreshOnRunStart(status: Status | undefined): void {
  const client = useQueryClient();
  const known = status !== undefined;
  const runId = status?.current_run?.run_id ?? null;
  // undefined until a status has been seen
  const seen = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (!known) return;
    if (seen.current !== undefined && runId !== null && runId !== seen.current) {
      void client.invalidateQueries({ queryKey: ["runs"] });
    }
    seen.current = runId;
  }, [known, runId, client]);
}
