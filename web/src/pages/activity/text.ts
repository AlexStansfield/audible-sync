/** Activity's wording: a run's duration and note, a run's books, the log's levels and times. */
import type { components } from "../../api/schema";
import { formatBytes, formatElapsed, plural } from "../../lib/format";
import { isInterrupted, runDetail } from "../dashboard/text";

type SyncRun = components["schemas"]["SyncRunOut"];
type RunSnapshot = components["schemas"]["RunSnapshot"];
export type RunBook = components["schemas"]["RunBookOut"];
export type LogEntry = components["schemas"]["LogEntry"];

/** How long a run took, or has taken so far while it is the run in flight. */
export function runDuration(run: SyncRun, live: boolean, now: number): string {
  const started = Date.parse(run.started_at);
  if (run.finished_at) return formatElapsed((Date.parse(run.finished_at) - started) / 1000);
  return live ? formatElapsed((now - started) / 1000) : "—";
}

/** The note beside a run: what it is doing, or why it stopped. */
export function runNote(run: SyncRun, current: RunSnapshot | null | undefined): string {
  if (run.outcome === "running") {
    if (isInterrupted(run, current?.run_id)) return "Interrupted: the service stopped during this run";
    if (current?.stage === "syncing") return "Reading the library from Audible";
    if (current?.stage === "cancelling") return "Cancelling";
    return current?.book?.title ? `Downloading ${current.book.title}` : "In progress";
  }
  // The Dashboard's words for the same run
  if (run.outcome === "cancelled") return runDetail(run, current?.run_id);
  return run.error ?? "—";
}

/** `14 runs recorded`, or `3 runs for Alex (UK)` with an account chosen. */
export function historySubtitle(total: number, accountName: string | null): string {
  return accountName ? `${plural(total, "run")} for ${accountName}` : `${plural(total, "run")} recorded`;
}

/** `1–20 of 148` */
export function shownText(shown: number, total: number): string {
  return shown === 0 ? `0 of ${total}` : `1–${shown} of ${total}`;
}

/** One book's line under its title: the time and size it took, or the error. */
export function runBookNote(book: RunBook, outcome: "downloaded" | "failed" | "refused"): string {
  if (outcome === "downloaded") {
    const parts = [
      book.seconds != null ? formatElapsed(book.seconds) : null,
      book.bytes != null ? formatBytes(book.bytes) : null,
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : "Downloaded";
  }
  return book.error ?? (outcome === "refused" ? "Audible refused the licence" : "No error recorded");
}

export const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;
export type Level = (typeof LEVELS)[number];

export const LEVEL_COLOUR: Record<string, string> = {
  DEBUG: "var(--muted)",
  INFO: "var(--info)",
  WARNING: "var(--accent-text)",
  ERROR: "var(--bad)",
  CRITICAL: "var(--bad)",
};

/** A log line's time as a local `14:06:41`; the full moment goes in its title. */
export function logTime(iso: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}
