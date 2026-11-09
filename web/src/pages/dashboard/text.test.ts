import { describe, expect, it } from "vitest";
import { status } from "../../test/fakeApi";
import { run } from "../../test/fixtures";
import {
  bookOfRun,
  isInterrupted,
  nextRunText,
  runDetail,
  stageLabel,
  subtitle,
  unavailableText,
  upNextHeader,
} from "./text";

const scheduler = status().scheduler;
const NOW = new Date("2026-11-02T14:20:00Z");

describe("subtitle", () => {
  it("counts the accounts and gives the schedule", () => {
    expect(subtitle(2, scheduler)).toBe("2 accounts · scheduled every 6 hours");
    expect(subtitle(1, { ...scheduler, interval_minutes: 90 })).toBe("1 account · scheduled every 90 minutes");
    expect(subtitle(1, { ...scheduler, enabled: false })).toBe("1 account · schedule off");
    expect(subtitle(0, scheduler)).toBe("No accounts yet");
  });
});

describe("runDetail", () => {
  it.each([
    [run(), "2 added · 2 downloaded"],
    [run({ books_added: 0, books_downloaded: 5, books_failed: 1, outcome: "partial" }), "5 downloaded · 1 failed"],
    [run({ books_added: 0, books_downloaded: 0 }), "No new purchases"],
    [run({ outcome: "cancelled", books_downloaded: 3 }), "Stopped after 3 books"],
    [run({ outcome: "cancelled", books_downloaded: 0, books_failed: 0 }), "Stopped before finishing a book"],
    [run({ outcome: "aborted", error: "5 licence refusals in a row" }), "5 licence refusals in a row"],
    [
      run({ outcome: "partial", books_added: 0, books_downloaded: 0, error: "No space left on device" }),
      "No space left on device",
    ],
    [run({ outcome: "partial", books_downloaded: 3, books_added: 0, error: "disk full" }), "3 downloaded · disk full"],
  ])("%#", (value, text) => {
    expect(runDetail(value)).toBe(text);
  });

  it("tells the run in flight from one the service never closed", () => {
    const row = run({ id: 148, outcome: "running", finished_at: null });
    expect(runDetail(row, 148)).toBe("In progress");
    expect(isInterrupted(row, 148)).toBe(false);
    expect(runDetail(row, null)).toBe("Interrupted: the service stopped during this run");
    expect(isInterrupted(row, 149)).toBe(true);
    expect(isInterrupted(run(), null)).toBe(false);
  });
});

describe("stageLabel", () => {
  const live = { run_id: 1, started_at: "", account: null, book: null, books_done: 0, books_total: 0, transfer: null };

  it("says a full refresh while it reads the library, and downloads like any run", () => {
    expect(stageLabel({ ...live, stage: "syncing", full_refresh: false })).toBe("Syncing");
    expect(stageLabel({ ...live, stage: "syncing", full_refresh: true })).toBe("Refreshing all");
    expect(stageLabel({ ...live, stage: "downloading", full_refresh: true })).toBe("Downloading");
    expect(stageLabel({ ...live, stage: "cancelling", full_refresh: true })).toBe("Cancelling");
  });
});

describe("bookOfRun", () => {
  const snapshot = {
    run_id: 148,
    started_at: "2026-11-02T14:00:00+00:00",
    stage: "downloading" as const,
    account: null,
    book: null,
    full_refresh: false,
    books_done: 2,
    books_total: 10,
    transfer: null,
  };

  it("counts the book in hand", () => {
    expect(bookOfRun(snapshot)).toBe("Book 3 of 10 this run");
    expect(bookOfRun({ ...snapshot, books_total: 0 })).toBeNull();
  });
});

describe("upNextHeader", () => {
  it("gives when and how much", () => {
    // 18:00 or 6:00 PM, as the locale has it
    expect(upNextHeader(scheduler, 10, NOW)).toMatch(/^Run at (18:00|0?6:00 PM) · up to 10 books per account$/);
    expect(upNextHeader(scheduler, null, NOW)).toMatch(/· everything waiting$/);
    expect(upNextHeader({ ...scheduler, running: true }, 1, NOW)).toBe("This run · up to 1 book per account");
    expect(upNextHeader({ ...scheduler, next_run_at: null }, 2, NOW)).toBe("Next run · up to 2 books per account");
  });
});

describe("nextRunText", () => {
  it("counts down, then says the run is due rather than in the past", () => {
    expect(nextRunText("2026-11-02T20:00:00Z", NOW)).toBe("in 5h 40m");
    expect(nextRunText("2026-11-02T14:20:00Z", NOW)).toBe("due now");
    expect(nextRunText("2026-11-02T14:18:00Z", NOW)).toBe("due now");
  });
});

describe("unavailableText", () => {
  it("says which unavailable books can be retried and which come back on their own", () => {
    expect(unavailableText(3, 3)).toBe("Audible refused the licence. They can be retried.");
    expect(unavailableText(16, 4)).toBe(
      "Audible refused the licence for 4, which can be retried; the 12 titles Audible withdrew from Plus come back on their own.",
    );
    expect(unavailableText(2, 1)).toBe(
      "Audible refused the licence for 1, which can be retried; the title Audible withdrew from Plus comes back on its own.",
    );
  });
});
