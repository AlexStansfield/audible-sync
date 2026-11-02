import { describe, expect, it } from "vitest";
import { run } from "../../test/fixtures";
import { historySubtitle, logTime, runBookNote, runDuration, runNote, shownText } from "./text";

const NOW = Date.parse("2026-11-02T08:20:05Z");
const LIVE = {
  run_id: 148,
  started_at: "2026-11-02T08:00:00+00:00",
  stage: "downloading" as const,
  account: null,
  book: { id: 7, asin: "B0", title: "Red Rising" },
  books_done: 2,
  books_total: 10,
  transfer: null,
};
const BOOK = {
  book_id: 7,
  asin: "B0",
  title: "Golden Son",
  cover_url: null,
  error: null,
  finished_at: "2026-11-02T08:10:00+00:00",
};

describe("Activity's wording", () => {
  it("times a finished run, and a running one only while it is the run in flight", () => {
    expect(runDuration(run({ finished_at: "2026-11-02T08:14:02+00:00" }), false, NOW)).toBe("14m 2s");
    expect(runDuration(run({ outcome: "running", finished_at: null }), true, NOW)).toBe("20m 5s");
    expect(runDuration(run({ outcome: "running", finished_at: null }), false, NOW)).toBe("—");
  });

  it("says what a run is doing or why it stopped, in the Dashboard's words", () => {
    const running = run({ id: 148, outcome: "running", finished_at: null });
    expect(runNote(running, LIVE, true)).toBe("Downloading Red Rising");
    expect(runNote(running, { ...LIVE, stage: "syncing", book: null }, true)).toBe("Reading the library from Audible");
    expect(runNote(running, { ...LIVE, stage: "cancelling" }, true)).toBe("Cancelling");
    expect(runNote(running, null, true)).toBe("Interrupted: the service stopped during this run");
    // Before the status has answered, the run in flight is not called interrupted
    expect(runNote(running, undefined, false)).toBe("In progress");
    expect(runNote(run({ outcome: "aborted", error: "5 licence refusals in a row" }), null, true)).toBe(
      "5 licence refusals in a row",
    );
    expect(runNote(run({ outcome: "failed", error: null }), null, true)).toBe("Stopped early");
    expect(runNote(run({ outcome: "cancelled", books_downloaded: 1, books_failed: 0 }), null, true)).toBe(
      "Stopped after 1 book",
    );
    expect(runNote(run(), null, true)).toBe("—");
    expect(runNote(run({ outcome: "partial", error: "Audible stopped answering" }), null, true)).toBe(
      "Audible stopped answering",
    );
  });

  it("counts the runs and the rows shown", () => {
    expect(historySubtitle(148, null)).toBe("148 runs recorded");
    expect(historySubtitle(1, "Alex (UK)")).toBe("1 run for Alex (UK)");
    expect(shownText(20, 148)).toBe("1–20 of 148");
  });

  it("notes each book of a run", () => {
    expect(runBookNote({ ...BOOK, seconds: 542, bytes: 611e6 }, "downloaded")).toBe("9m 2s · 611 MB");
    expect(runBookNote({ ...BOOK, seconds: null, bytes: null }, "downloaded")).toBe("Downloaded");
    expect(runBookNote({ ...BOOK, error: "ffmpeg exited with status 1", seconds: 3, bytes: null }, "failed")).toBe(
      "ffmpeg exited with status 1",
    );
    expect(runBookNote({ ...BOOK, seconds: null, bytes: null }, "refused")).toBe("Audible refused the licence");
  });

  it("gives a log line's time on a 24-hour clock", () => {
    expect(logTime("2026-11-02T14:06:41+00:00", "en-GB")).toBe("14:06:41");
  });
});
