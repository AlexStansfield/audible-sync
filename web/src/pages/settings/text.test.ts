import { describe, expect, it } from "vitest";
import { counts, system } from "../../test/fixtures";
import { averageBookBytes, diskFree, diskNote, diskUsedPercent, ffmpegText, scheduleText, unsavedText } from "./text";

const NOW = new Date("2026-11-02T12:00:00Z");
const SCHEDULER = { enabled: true, interval_minutes: 360, next_run_at: "2026-11-02T18:00:00+00:00", running: false };
const DISK = { path: "/audiobooks", free_bytes: 1.6e12, total_bytes: 4e12, low_space: false };
const SYSTEM = system({ audiobook_folder: DISK });

describe("Settings' wording", () => {
  it("counts the unsaved changes", () => {
    expect(unsavedText(1)).toBe("1 unsaved change");
    expect(unsavedText(2)).toBe("2 unsaved changes");
  });

  it("gives the next run from the saved schedule, or says a save moves it", () => {
    expect(scheduleText(SCHEDULER, false, NOW)).toMatch(/^Next run (18:00|06:00 PM)\. At least 5 minutes\.$/);
    expect(scheduleText({ ...SCHEDULER, next_run_at: "2026-11-02T11:00:00+00:00" }, false, NOW)).toBe(
      "Next run due now. At least 5 minutes.",
    );
    expect(scheduleText({ ...SCHEDULER, enabled: false, next_run_at: null }, false, NOW)).toBe(
      "No scheduled runs. At least 5 minutes.",
    );
    expect(scheduleText(SCHEDULER, true, NOW)).toBe(
      "The next run is worked out again when you save. At least 5 minutes.",
    );
  });

  it("works out the average book from the downloaded ones", () => {
    const stats = {
      accounts: [],
      books: counts({ downloaded: 4 }),
      bytes_on_disk: 1.64e9,
      last_run: null,
      next_run_at: null,
    };
    expect(averageBookBytes(stats)).toBe(410e6);
    expect(averageBookBytes({ ...stats, books: counts({}) })).toBeNull();
    expect(averageBookBytes(undefined)).toBeNull();
  });

  it("words the disk, and how many more books fit", () => {
    expect(diskFree(DISK)).toBe("1.6 TB free of 4 TB");
    expect(diskUsedPercent(DISK)).toBe(60);
    expect(diskNote(DISK, 410e6)).toBe("About 3,902 more books fit at your average size of 410 MB.");
    expect(diskNote(DISK, null)).toBe("");
    const low = { ...DISK, free_bytes: 38e9, low_space: true };
    expect(diskNote(low, 410e6)).toBe(
      "Space is low. About 92 more books fit at your average size; downloads will fail when the disk is full.",
    );
    expect(diskNote(low, null)).toBe("Space is low: downloads will fail when the disk is full.");
    const unread = { ...DISK, free_bytes: null, total_bytes: null };
    expect(diskFree(unread)).toBe("Free space unknown");
    expect(diskUsedPercent(unread)).toBeNull();
    expect(diskNote(unread, 410e6)).toBe("The service could not read this folder's disk.");
  });

  it("gives FFmpeg's version, and says when it cannot do the job", () => {
    expect(ffmpegText(SYSTEM)).toEqual({ version: "7.1.1", note: "libopus available", problem: false });
    expect(ffmpegText({ ...SYSTEM, libopus: false })).toEqual({
      version: "7.1.1",
      note: "no libopus: Ogg Opus will fail",
      problem: true,
    });
    expect(ffmpegText({ ...SYSTEM, ffmpeg_version: null })).toMatchObject({ version: "Not found", problem: true });
  });
});
