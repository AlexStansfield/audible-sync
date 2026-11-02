/**
 * The Dashboard's wording, kept out of the components so it can be tested on its own.
 */
import type { components } from "../../api/schema";
import { formatClock, formatInterval, plural } from "../../lib/format";

type SyncRun = components["schemas"]["SyncRunOut"];
type Scheduler = components["schemas"]["SchedulerStatus"];
type RunSnapshot = components["schemas"]["RunSnapshot"];

/** Under the title: `2 accounts · scheduled every 6 hours`, or `No accounts yet`. */
export function subtitle(accounts: number, scheduler: Scheduler): string {
  if (accounts === 0) return "No accounts yet";
  const schedule = scheduler.enabled ? `scheduled ${formatInterval(scheduler.interval_minutes)}` : "schedule off";
  return `${plural(accounts, "account")} · ${schedule}`;
}

/** One line about what a run did: `2 added · 2 downloaded`, `No new purchases`. */
export function runDetail(run: SyncRun): string {
  switch (run.outcome) {
    case "running":
      return "In progress";
    case "cancelled":
      return `Stopped after ${plural(run.books_downloaded + run.books_failed, "book")}`;
    case "failed":
    case "aborted":
      return run.error ?? "Stopped early";
    default: {
      const parts = [
        run.books_added && `${run.books_added} added`,
        run.books_downloaded && `${run.books_downloaded} downloaded`,
        run.books_failed && `${run.books_failed} failed`,
      ].filter(Boolean);
      return parts.length ? parts.join(" · ") : "No new purchases";
    }
  }
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

/** The result of Retry unavailable. */
export function requeuedText(requeued: number): string {
  if (requeued === 0) {
    return "Nothing to retry: the books left are titles Audible withdrew, which come back on their own.";
  }
  return `${plural(requeued, "book")} queued again for the next run.`;
}
