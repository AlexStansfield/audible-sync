/**
 * The Dashboard's wording, kept out of the components so it can be tested on its own.
 */
import type { components } from "../../api/schema";
import { formatClock, formatInterval, formatRelative, plural } from "../../lib/format";

type SyncRun = components["schemas"]["SyncRunOut"];
type Scheduler = components["schemas"]["SchedulerStatus"];
type RunSnapshot = components["schemas"]["RunSnapshot"];

/** Under the title: `2 accounts · scheduled every 6 hours`, or `No accounts yet`. */
export function subtitle(accounts: number, scheduler: Scheduler): string {
  if (accounts === 0) return "No accounts yet";
  const schedule = scheduler.enabled ? `scheduled ${formatInterval(scheduler.interval_minutes)}` : "schedule off";
  return `${plural(accounts, "account")} · ${schedule}`;
}

/**
 * A `running` row that is not the run in flight: the service stopped part way (killed,
 * out of memory) and the row was never closed.
 */
export function isInterrupted(run: SyncRun, liveRunId: number | null | undefined): boolean {
  return run.outcome === "running" && run.id !== liveRunId;
}

/** One line about what a run did: `2 added · 2 downloaded`, `No new purchases`. */
export function runDetail(run: SyncRun, liveRunId?: number | null): string {
  switch (run.outcome) {
    case "running":
      return isInterrupted(run, liveRunId) ? "Interrupted: the service stopped during this run" : "In progress";
    case "cancelled": {
      // A cancel during the first book stops the run with none finished (found live, 2026-11-02)
      const finished = run.books_downloaded + run.books_failed;
      return finished ? `Stopped after ${plural(finished, "book")}` : "Stopped before finishing a book";
    }
    case "failed":
    case "aborted":
      return run.error ?? "Stopped early";
    default: {
      const parts = [
        run.books_added && `${run.books_added} added`,
        run.books_downloaded && `${run.books_downloaded} downloaded`,
        run.books_failed && `${run.books_failed} failed`,
      ].filter(Boolean);
      // A partial run can carry an error raised after the sync, whatever the counts say
      if (run.error) return [...parts, run.error].join(" · ");
      return parts.length ? parts.join(" · ") : "No new purchases";
    }
  }
}

/** How far off the next run is: `in 5h 40m`, or `due now` once its time has come. */
export function nextRunText(iso: string, now: Date = new Date()): string {
  return new Date(iso).getTime() <= now.getTime() ? "due now" : formatRelative(iso, now);
}

/** The run card's stage chip. */
export function stageLabel(run: RunSnapshot): string {
  if (run.stage === "cancelling") return "Cancelling";
  if (run.stage === "downloading") return "Downloading";
  return "Syncing";
}

/** "Book 3 of 10 this run": the book in hand, counting the slots already used. */
export function bookOfRun(run: RunSnapshot): string | null {
  if (run.books_total <= 0) return null;
  return `Book ${Math.min(run.books_done + 1, run.books_total)} of ${run.books_total} this run`;
}

/** Up next's header: when it runs and how much it takes. */
export function upNextHeader(scheduler: Scheduler, maxDownload: number | null, now: Date = new Date()): string {
  const when = scheduler.running
    ? "This run"
    : scheduler.next_run_at
      ? `Run at ${formatClock(scheduler.next_run_at, now)}`
      : "Next run";
  const limit = maxDownload == null ? "everything waiting" : `up to ${plural(maxDownload, "book")} per account`;
  return `${when} · ${limit}`;
}

/**
 * Needs attention's line under "N books unavailable": the licence refusals Retry
 * unavailable can queue again (`retryable_unavailable`), and the titles Audible withdrew,
 * which the sync brings back on its own.
 */
export function unavailableText(unavailable: number, retryable: number): string {
  const withdrawn = unavailable - retryable;
  if (withdrawn <= 0) return "Audible refused the licence. They can be retried.";
  const rest =
    withdrawn === 1
      ? "the title Audible withdrew from Plus comes back on its own"
      : `the ${withdrawn.toLocaleString()} titles Audible withdrew from Plus come back on their own`;
  return `Audible refused the licence for ${retryable.toLocaleString()}, which can be retried; ${rest}.`;
}
