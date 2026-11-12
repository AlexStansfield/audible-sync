/**
 * The search box's prefixes, shared by Search and Library (DESIGN.md, "The search box
 * and its prefixes"): `author:`, `narrator:`, `series:` and `title:`. A prefix counts
 * at the start of a word, in any case. Its value runs to the next prefix or the end, so
 * no quotes are needed; whatever comes before the first prefix is keywords. Only those
 * four words count, so "Star Wars: Trials of the Jedi" is keywords as written.
 *
 * The box keeps what was typed; this is how it is read.
 */

export type Field = "author" | "narrator" | "series" | "title";
export const FIELDS: readonly Field[] = ["author", "narrator", "series", "title"];

export type Parsed = { keywords: string } & Partial<Record<Field, string>>;

/** A part of the search as a chip shows it, in the order of the box. */
export type Part = { field: Field | "keywords"; value: string };

export const FIELD_LABEL: Record<Field | "keywords", string> = {
  keywords: "Keywords",
  author: "Author",
  narrator: "Narrator",
  series: "Series",
  title: "Title",
};

const PREFIX = /(^|\s)(author|narrator|series|title):/gi;

/** The parts of a search, in the order they were typed; a field given twice keeps the last. */
export function searchParts(text: string): Part[] {
  const parts: Part[] = [];
  const marks = [...text.matchAll(PREFIX)].map((match) => ({
    field: (match[2] ?? "").toLowerCase() as Field,
    // Where the prefix word starts, and where its value does
    start: (match.index ?? 0) + (match[1] ?? "").length,
    valueStart: (match.index ?? 0) + match[0].length,
  }));
  const keywords = clean(text.slice(0, marks[0]?.start ?? text.length));
  if (keywords) parts.push({ field: "keywords", value: keywords });
  marks.forEach((mark, index) => {
    const end = marks[index + 1]?.start ?? text.length;
    const value = clean(text.slice(mark.valueStart, end));
    if (!value) return;
    const earlier = parts.findIndex((part) => part.field === mark.field);
    if (earlier >= 0) parts.splice(earlier, 1);
    parts.push({ field: mark.field, value });
  });
  return parts;
}

/** The search as fields: what the API is asked. */
export function parseSearch(text: string): Parsed {
  const parsed: Parsed = { keywords: "" };
  for (const part of searchParts(text)) {
    if (part.field === "keywords") parsed.keywords = part.value;
    else parsed[part.field] = part.value;
  }
  return parsed;
}

/** Whether the search has anything in it at all. */
export function isEmptySearch(text: string): boolean {
  return searchParts(text).length === 0;
}

/** The search written back as text: keywords first, then each prefix. */
export function composeSearch(parts: Part[]): string {
  const keywords = parts.find((part) => part.field === "keywords");
  const rest = parts.filter((part) => part.field !== "keywords").map((part) => `${part.field}:${part.value}`);
  return [keywords?.value, ...rest].filter(Boolean).join(" ");
}

/** The search without one of its parts, as removing its chip leaves it. */
export function withoutPart(text: string, field: Part["field"]): string {
  return composeSearch(searchParts(text).filter((part) => part.field !== field));
}

/** The search in words, for a "nothing matches" line: `author John Scalzi with the keywords “zombies”`. */
export function describeSearch(text: string): string {
  const parts = searchParts(text);
  const fields = parts
    .filter((part) => part.field !== "keywords")
    .map((part) =>
      part.field === "author" || part.field === "narrator"
        ? `${part.field} ${part.value}`
        : `${part.field} “${part.value}”`,
    );
  const keywords = parts.find((part) => part.field === "keywords");
  const named = fields.join(" and ");
  if (!keywords) return named;
  return named ? `${named} with the keywords “${keywords.value}”` : `“${keywords.value}”`;
}

function clean(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
