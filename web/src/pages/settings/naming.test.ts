import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { type NamingBook, outputPath, primarySeries, sanitizeFilename, templateError } from "./naming";

// The cases tests/test_naming.py runs against src/audible_sync/pipeline/naming.py, read from the same file
interface Cases {
  books: Record<string, NamingBook>;
  sanitize: { input: string | null; fallback?: string; expected: string }[];
  paths: {
    book: string;
    folder_template: string;
    filename_template: string;
    segments?: string[];
    stem?: string;
    error?: string;
  }[];
}
const CASES: Cases = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../../../../tests/fixtures/naming_cases.json"), "utf-8"),
);

describe("the naming port, on the service's own cases", () => {
  it.each(CASES.sanitize.map((c) => [JSON.stringify(c.input).slice(0, 30), c] as const))("sanitizes %s", (_, c) => {
    expect(sanitizeFilename(c.input, c.fallback ?? "Unknown")).toBe(c.expected);
  });

  it.each(CASES.paths.map((c) => [`${c.book}: ${c.folder_template} | ${c.filename_template}`, c] as const))(
    "files %s",
    (_, c) => {
      const book = CASES.books[c.book] as NamingBook;
      const error = templateError(c.folder_template, c.filename_template);
      if (c.error) {
        expect(error).toBe(c.error);
        return;
      }
      expect(error).toBeNull();
      expect(outputPath(book, c.folder_template, c.filename_template)).toEqual({ segments: c.segments, stem: c.stem });
    },
  );
});

describe("primarySeries", () => {
  it("is null for no series, or only untitled ones", () => {
    expect(primarySeries([])).toBeNull();
    expect(primarySeries(undefined)).toBeNull();
    expect(primarySeries([{ title: null, sequence: "1" }])).toBeNull();
  });

  it("does not depend on the order the entries come in", () => {
    const a = { title: "Ringworld", sequence: "1" };
    const b = { title: "Known Space", sequence: "4" };
    expect(primarySeries([a, b])).toEqual(primarySeries([b, a]));
  });
});
