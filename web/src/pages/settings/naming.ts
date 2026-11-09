/**
 * The Settings preview's port of `src/audible_sync/pipeline/naming.py`: the same sanitizer, template engine
 * and primary-series rule, so the two sample paths show what the service would file.
 * Both suites run `tests/fixtures/naming_cases.json`, which is what keeps the two in
 * step - change one, change the other and the cases with it.
 */

/** The book fields the templates read. */
export interface NamingBook {
  asin: string;
  title: string | null;
  subtitle?: string | null;
  authors?: string[];
  narrators?: string[];
  series?: { title?: string | null; sequence?: string | null; series_asin?: string | null }[];
  release_date?: string | null;
}

export const PLACEHOLDERS = [
  "author",
  "authors",
  "narrator",
  "narrators",
  "title",
  "subtitle",
  "series",
  "sequence",
  "year",
  "asin",
] as const;

const MAX_NAME_LENGTH = 150;
const MAX_NAME_BYTES = 200;
// Python's `\s` on text, which is wider than JavaScript's (\x1c-\x1f, \x85) and does
// not take in ﻿: a JavaScript `\s` would sanitize those characters differently
const SPACE = "[\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const SPACES = new RegExp(`${SPACE}+`, "g");
// biome-ignore lint/suspicious/noControlCharactersInRegex: the control characters are what it removes
const INVALID = /[<>"|?*\x00-\x1f]/g;
const RESERVED = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  ...Array.from({ length: 9 }, (_, n) => `COM${n + 1}`),
  ...Array.from({ length: 9 }, (_, n) => `LPT${n + 1}`),
]);
const OPTIONAL_GROUP = /\[([^[\]]*)\]/g;
const PLACEHOLDER = /\{([^{}]*)\}/g;

/** Python's `str.strip(" .")`, or `rstrip` with `end` alone. */
function stripSpaceDot(value: string, start = true): string {
  return value.replace(start ? /^[ .]+|[ .]+$/g : /[ .]+$/, "");
}

/** At most `limit` bytes of UTF-8, never splitting a character. */
function truncateToBytes(value: string, limit = MAX_NAME_BYTES): string {
  let bytes = 0;
  let out = "";
  for (const char of value) {
    const code = char.codePointAt(0) ?? 0;
    const size = code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
    if (bytes + size > limit) break;
    bytes += size;
    out += char;
  }
  return out;
}

/** `sanitize_filename`: one path segment that is safe on Linux, macOS, Windows and SMB. */
export function sanitizeFilename(name: string | null | undefined, fallback = "Unknown"): string {
  if (name == null) return fallback;
  let result = name.replaceAll(":", " -").replace(/[/\\]/g, "-");
  result = result.replace(SPACES, " ").replace(INVALID, "");
  result = stripSpaceDot(result.replace(SPACES, " "));
  // Characters, as Python counts them: code points, not UTF-16 units
  result = stripSpaceDot(truncateToBytes(Array.from(result).slice(0, MAX_NAME_LENGTH).join("")), false);
  if (RESERVED.has((result.split(".")[0] ?? "").toUpperCase())) result = `${result}_`;
  return result || fallback;
}

/** Python's `float()` on a sequence; anything else sorts last, as `math.inf` does. */
function sequenceNumber(value: string | null | undefined): number {
  if (value == null) return Number.POSITIVE_INFINITY;
  const text = value.trim();
  if (/^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(text)) return Number(text);
  if (/^[+-]?inf(inity)?$/i.test(text)) return text.startsWith("-") ? Number.NEGATIVE_INFINITY : Infinity;
  return Number.POSITIVE_INFINITY;
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

type SeriesEntry = NonNullable<NamingBook["series"]>[number];

/** `_series_sort_key` as a comparison: sequence, then title, then series ASIN. */
function compareSeries(a: SeriesEntry, b: SeriesEntry): number {
  const [x, y] = [sequenceNumber(a.sequence), sequenceNumber(b.sequence)];
  if (x !== y) return x < y ? -1 : 1;
  return compareText(a.title ?? "", b.title ?? "") || compareText(a.series_asin ?? "", b.series_asin ?? "");
}

/** `Book.primary_series`: of the entries with a title, the first by `compareSeries`. */
export function primarySeries(series: NamingBook["series"] = []): { title: string; sequence: string | null } | null {
  let best: SeriesEntry | null = null;
  for (const entry of series) {
    if (entry.title && (best === null || compareSeries(entry, best) < 0)) best = entry;
  }
  return best?.title ? { title: best.title, sequence: best.sequence ?? null } : null;
}

/** `book_template_values`: every placeholder's path-safe value. */
export function templateValues(book: NamingBook): Record<string, string> {
  const clean = (value: string | null | undefined) =>
    value == null || value === "" ? "" : sanitizeFilename(value, "");
  const authors = book.authors ?? [];
  const narrators = book.narrators ?? [];
  const series = primarySeries(book.series);
  return {
    asin: book.asin,
    title: sanitizeFilename(book.title, book.asin),
    subtitle: clean(book.subtitle),
    author: authors.length ? sanitizeFilename(authors[0], "Unknown Author") : "Unknown Author",
    authors: authors.length ? sanitizeFilename(authors.join(", "), "Unknown Author") : "Unknown Author",
    narrator: narrators.length ? clean(narrators[0]) : "",
    narrators: clean(narrators.join(", ")),
    series: clean(series?.title),
    sequence: clean(series?.sequence),
    year: clean((book.release_date ?? "").slice(0, 4)),
  };
}

function placeholderValue(name: string, values: Record<string, string>, template: string): string {
  if (!(PLACEHOLDERS as readonly string[]).includes(name)) {
    const valid = [...PLACEHOLDERS]
      .sort()
      .map((p) => `{${p}}`)
      .join(", ");
    throw new Error(`Unknown placeholder '{${name}}' in naming template '${template}'. Valid placeholders: ${valid}`);
  }
  return values[name] ?? "";
}

/** `render_template`: `[...]` groups only when every placeholder in them has a value. */
export function renderTemplate(template: string, values: Record<string, string>): string {
  const resolved = template.replace(OPTIONAL_GROUP, (_match, content: string) => {
    const names = Array.from(content.matchAll(PLACEHOLDER), (m) => m[1] ?? "");
    // Every name is looked up, as Python's `any` does until the first empty one
    for (const name of names) if (!placeholderValue(name, values, template)) return "";
    return content;
  });
  return resolved.replace(PLACEHOLDER, (_match, name: string) => placeholderValue(name, values, template));
}

/** `validate_templates`: the service's message for a template it would refuse, or null. */
export function templateError(folderTemplate: string, filenameTemplate: string): string | null {
  if (filenameTemplate.includes("/") || filenameTemplate.includes("\\")) {
    return `naming filename template '${filenameTemplate}' must not contain '/' or '\\'`;
  }
  const dummy = Object.fromEntries(PLACEHOLDERS.map((p) => [p, "x"]));
  try {
    renderTemplate(folderTemplate, dummy);
    renderTemplate(filenameTemplate, dummy);
  } catch (error) {
    return (error as Error).message;
  }
  return null;
}

/** `book_output_paths`: the folder's segments under the library, and the file's stem. */
export function outputPath(
  book: NamingBook,
  folderTemplate: string,
  filenameTemplate: string,
): { segments: string[]; stem: string } {
  const values = templateValues(book);
  const segments = renderTemplate(folderTemplate, values)
    .split("/")
    .map((segment) => sanitizeFilename(segment, ""))
    .filter(Boolean);
  return {
    segments: segments.length ? segments : [book.asin],
    stem: sanitizeFilename(renderTemplate(filenameTemplate, values), book.asin),
  };
}
