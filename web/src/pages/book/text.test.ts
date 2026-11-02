import { describe, expect, it } from "vitest";
import { book } from "../../test/fixtures";
import { attemptsText, fileRows, formatLabel, listeningText, problem, seriesLabel, transferText } from "./text";

describe("Book detail's wording", () => {
  it("names the series the book files under", () => {
    expect(seriesLabel(book({ primary_series: { title: "Impossible Times", sequence: "1" } }))).toBe(
      "Impossible Times #1",
    );
    expect(seriesLabel(book({ primary_series: { title: "Discworld", sequence: null } }))).toBe("Discworld");
    expect(seriesLabel(book({ primary_series: null }))).toBeNull();
  });

  it("says how far the listening has got", () => {
    expect(listeningText(book({ percent_complete: 42.4 }))).toBe("42%");
    expect(listeningText(book({ percent_complete: 0 }))).toBe("Not started");
    expect(listeningText(book({ is_finished: true, percent_complete: 100 }))).toBe("Finished");
  });

  it("words the format and the attempts", () => {
    expect(formatLabel("m4b")).toBe("M4B (AAC, original)");
    expect(formatLabel("oga")).toBe("Ogg Opus");
    expect(formatLabel(null)).toBe("—");
    expect(attemptsText(1, 3)).toBe("1 of 3");
    expect(attemptsText(1, undefined)).toBe("1");
  });

  it("explains why a book is not downloaded", () => {
    expect(problem(book({ status: "failed", attempts: 3, last_error: "ffmpeg exited with status 1" }))).toEqual({
      title: "Gave up after 3 attempts",
      error: "ffmpeg exited with status 1",
    });
    expect(problem(book({ status: "unavailable", last_error: "License not granted to customer" }))).toEqual({
      title: "Audible refused the licence",
      error: "License not granted to customer",
    });
    expect(problem(book({ status: "unavailable", is_consumable: false }))).toEqual({
      title: "Audible withdrew this title",
      error: null,
      note: "It comes back on its own when Audible offers it again.",
    });
    expect(problem(book({ status: "waiting_download", last_error: "timed out" }))?.title).toMatch(/tried again/);
    expect(problem(book({ status: "waiting_download" }))).toBeNull();
    expect(problem(book({ status: "downloaded" }))).toBeNull();
  });

  it("lists the files, or why each is missing", () => {
    const filed = fileRows(
      book({ status: "downloaded", file_path: "/a/b.m4b", cover_path: "/a/b_cover.jpg", has_pdf: false }),
    );
    expect(filed.map((row) => row.path ?? row.note)).toEqual(["/a/b.m4b", "/a/b_cover.jpg", "—", "No companion PDF"]);
    expect(fileRows(book({ status: "downloaded" }))[0]?.note).toBe("Filed before its path was recorded");
    expect(fileRows(book({ status: "waiting_download", has_pdf: true })).map((row) => row.note)).toEqual([
      "Not downloaded",
      "—",
      "—",
      "Not downloaded",
    ]);
  });

  it("words the transfer under the live bar", () => {
    expect(transferText({ desc: "Dune", bytes: 243e6, total: 405e6 })).toBe("243 MB of 405 MB");
    expect(transferText({ desc: "Dune", bytes: 243e6, total: null })).toBe("243 MB so far · size unknown");
    expect(transferText({ desc: "PDF", bytes: 1, total: 2 })).toBe("Downloading the companion PDF");
    expect(transferText({ desc: "Cover", bytes: 1, total: 2 })).toBe("Downloading the cover");
    expect(transferText(null)).toBeNull();
  });
});
