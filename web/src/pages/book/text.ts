/**
 * Book detail's wording, kept out of the component so it can be tested on its own.
 */
import type { components } from "../../api/schema";
import { formatBytes, plural } from "../../lib/format";
import { ACCESSORIES } from "../library/live";

type Book = components["schemas"]["BookOut"];
type Transfer = components["schemas"]["Transfer"];

/** `Impossible Times #1`, from the series the book files under (never `series[0]`). */
export function seriesLabel(book: Book): string | null {
  const series = book.primary_series;
  if (!series?.title) return null;
  return series.sequence ? `${series.title} #${series.sequence}` : series.title;
}

/** `42%`, `Finished`, `Not started`. */
export function listeningText(book: Book): string {
  if (book.is_finished) return "Finished";
  const percent = Math.round(book.percent_complete ?? 0);
  return percent > 0 ? `${percent}%` : "Not started";
}

const FORMATS: Record<string, string> = {
  m4b: "M4B (AAC, original)",
  oga: "Ogg Opus",
};

export function formatLabel(format: string | null): string {
  if (!format) return "—";
  return FORMATS[format] ?? format.toUpperCase();
}

/** `1 of 3`; just the count when the setting is not known yet. */
export function attemptsText(attempts: number, maxAttempts: number | undefined): string {
  return maxAttempts === undefined ? String(attempts) : `${attempts} of ${maxAttempts}`;
}

export type Problem = {
  title: string;
  /** What Audible or the pipeline said, shown as it was */
  error: string | null;
  /** What happens next, in the app's own words */
  note?: string;
};

/**
 * The red box in the Download card: why a book is not downloaded, with the error it
 * last met. `null` when there is nothing to explain.
 */
export function problem(book: Book): Problem | null {
  switch (book.status) {
    case "failed":
      return { title: `Gave up after ${plural(book.attempts, "attempt")}`, error: book.last_error };
    case "unavailable":
      // A withdrawn Plus title comes back by itself; a refused licence needs a retry
      return book.is_consumable
        ? { title: "Audible refused the licence", error: book.last_error }
        : {
            title: "Audible withdrew this title",
            error: book.last_error,
            note: "It comes back on its own when Audible offers it again.",
          };
    case "waiting_download":
      return book.last_error
        ? { title: "The last attempt failed; it is tried again on the next run", error: book.last_error }
        : null;
    default:
      return null;
  }
}

export type FileRow = { kind: string; path: string | null; note: string };

/** The Files on disk card: each file the book has, or why there is none. */
export function fileRows(book: Book): FileRow[] {
  const downloaded = book.status === "downloaded";
  return [
    {
      kind: "Audio",
      path: book.file_path,
      // A book downloaded before the path was recorded is still on disk, filed by the templates
      note: downloaded ? "Filed before its path was recorded" : "Not downloaded",
    },
    { kind: "Cover", path: book.cover_path, note: "—" },
    { kind: "Annotations", path: book.annotations_path, note: "—" },
    { kind: "PDF", path: book.pdf_path, note: book.has_pdf ? "Not downloaded" : "No companion PDF" },
  ];
}

/**
 * Under the live progress bar: `243 MB of 405 MB`, `243 MB so far · size unknown`, the
 * PDF or cover after the audio, and nothing while it decrypts.
 */
export function transferText(transfer: Transfer | null | undefined): string | null {
  if (!transfer) return null;
  const accessory = ACCESSORIES.get(transfer.desc ?? "");
  if (accessory) return `Downloading ${accessory}`;
  return transfer.total
    ? `${formatBytes(transfer.bytes)} of ${formatBytes(transfer.total)}`
    : `${formatBytes(transfer.bytes)} so far · size unknown`;
}
