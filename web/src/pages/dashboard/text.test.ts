import { describe, expect, it } from "vitest";
import { status } from "../../test/fakeApi";
import { run } from "../../test/fixtures";
import { bookOfRun, requeuedText, runDetail, subtitle, upNextHeader } from "./text";

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
    [run({ outcome: "aborted", error: "5 licence refusals in a row" }), "5 licence refusals in a row"],
    [run({ outcome: "running", finished_at: null }), "In progress"],
  ])("%#", (value, text) => {
    expect(runDetail(value)).toBe(text);
  });
});

describe("bookOfRun", () => {
  const snapshot = {
    run_id: 148,
    started_at: "2026-11-02T14:00:00+00:00",
    stage: "downloading" as const,
    account: null,
    book: null,
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

describe("requeuedText", () => {
  it("says how many went back, or why none did", () => {
    expect(requeuedText(3)).toBe("3 books queued again for the next run.");
    expect(requeuedText(0)).toMatch(/^Nothing to retry/);
  });
});
