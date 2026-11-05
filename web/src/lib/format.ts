/**
 * Formatting shared by every screen. Times come from the API in UTC (ISO 8601, with a
 * `Z` or `+00:00`) and are shown in the browser's time zone, relative when recent.
 */

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** Bytes in decimal units, as disk tools label them: `0 B`, `980 KB`, `243 MB`, `1.2 GB`. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 1000) {
    return `${Math.max(0, Math.round(bytes || 0))} B`;
  }
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  // Three significant figures at most: 1.2 GB, 24.5 GB, 243 MB. Rounded first, so
  // 999.6 MB, which rounds to 1000, reads 1 GB rather than 1000 MB
  let text = value >= 100 ? value.toFixed(0) : value.toFixed(1);
  if (Number(text) >= 1000 && unit < UNITS.length - 1) {
    unit += 1;
    text = (Number(text) / 1000).toFixed(1);
  }
  return `${text.replace(/\.0$/, "")} ${UNITS[unit]}`;
}

/** A book's length in minutes as `7h 6m`, `45m`; empty for none. */
export function formatLength(minutes: number | null | undefined): string {
  if (minutes == null || minutes <= 0) {
    return "";
  }
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/** An elapsed time in seconds as `12s`, `2m 14s`, `1h 5m`. */
export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) {
    const rest = total % 60;
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * A moment as a local time: `Today 08:00`, `Yesterday 21:14`, `Mon 08:00` within the
 * week, `3 Oct 08:00` this year, `3 Oct 2025` before.
 */
export function formatTime(iso: string, now: Date = new Date(), locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const time = date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  if (days === -1) return `Tomorrow ${time}`;
  if (days > 1 && days < 7) {
    return `${date.toLocaleDateString(locale, { weekday: "short" })} ${time}`;
  }
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.toLocaleDateString(locale, { day: "numeric", month: "short" })} ${time}`;
  }
  return date.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

/** A clock time today (`20:00`), else the fuller `formatTime`. */
export function formatClock(iso: string, now: Date = new Date(), locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  if (startOfDay(date) === startOfDay(now)) {
    return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  }
  return formatTime(iso, now, locale);
}

/** A short date: `1 Nov` this year, `1 Nov 2024` before. */
export function formatDay(iso: string, now: Date = new Date(), locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(locale, { day: "numeric", month: "short", year: sameYear ? undefined : "numeric" });
}

/** A schedule's interval: `every 6 hours`, `every hour`, `every 90 minutes`. */
export function formatInterval(minutes: number): string {
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return hours === 1 ? "every hour" : `every ${hours} hours`;
  }
  return `every ${minutes} minutes`;
}

/** A date alone: `3 Oct 2025`. */
export function formatDate(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

function span(ms: number): string {
  if (ms < HOUR) return `${Math.max(1, Math.round(ms / MINUTE))} min`;
  if (ms < DAY) {
    const hours = Math.floor(ms / HOUR);
    const minutes = Math.round((ms % HOUR) / MINUTE);
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.round(ms / DAY);
  return `${days} day${days === 1 ? "" : "s"}`;
}

/** How far a moment is from now: `in 5h 40m`, `12 min ago`, `just now`. */
export function formatRelative(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const diff = date.getTime() - now.getTime();
  if (Math.abs(diff) < 45_000) return diff > 0 ? "in a moment" : "just now";
  return diff > 0 ? `in ${span(diff)}` : `${span(-diff)} ago`;
}

export type BookStatus = "waiting_download" | "downloading" | "downloaded" | "unavailable" | "failed";
export type RunOutcome = "running" | "success" | "partial" | "failed" | "cancelled" | "aborted";

/** One word and one colour per book status, the same on every screen (DESIGN.md, Statuses). */
export const BOOK_STATUS: Record<BookStatus, { label: string; colour: string }> = {
  downloaded: { label: "Downloaded", colour: "var(--ok)" },
  waiting_download: { label: "Waiting", colour: "var(--info)" },
  downloading: { label: "Downloading", colour: "var(--accent-text)" },
  unavailable: { label: "Unavailable", colour: "var(--violet)" },
  failed: { label: "Failed", colour: "var(--bad)" },
};

export const RUN_OUTCOME: Record<RunOutcome, { label: string; colour: string }> = {
  success: { label: "Success", colour: "var(--ok)" },
  partial: { label: "Partial", colour: "var(--accent-text)" },
  running: { label: "Running", colour: "var(--accent-text)" },
  failed: { label: "Failed", colour: "var(--bad)" },
  aborted: { label: "Aborted", colour: "var(--bad)" },
  cancelled: { label: "Cancelled", colour: "var(--text-2)" },
};

/** A count with its noun: `1 book`, `3 books`. */
export function plural(count: number, noun: string, many = `${noun}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? noun : many}`;
}
