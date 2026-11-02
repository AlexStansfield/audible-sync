import { describe, expect, it } from "vitest";
import { formatBytes, formatDate, formatElapsed, formatLength, formatRelative, formatTime, plural } from "./format";

// The test run's zone is UTC (vite.config.ts), so these are the browser's local times
const NOW = new Date("2026-11-02T14:30:00Z");

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [999, "999 B"],
    [1000, "1 KB"],
    [980_000, "980 KB"],
    [243_000_000, "243 MB"],
    [1_234_000_000, "1.2 GB"],
    [24_560_000_000, "24.6 GB"],
    [3_000_000_000_000, "3 TB"],
    // Just under a boundary: rounded first, so never "1000 KB"
    [999_400, "999 KB"],
    [999_999, "1 MB"],
    [999_600_000, "1 GB"],
  ])("%d bytes is %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});

describe("formatLength", () => {
  it.each([
    [426, "7h 6m"],
    [45, "45m"],
    [120, "2h"],
    [0, ""],
    [null, ""],
  ])("%s minutes is %j", (minutes, text) => {
    expect(formatLength(minutes)).toBe(text);
  });
});

describe("formatElapsed", () => {
  it.each([
    [12, "12s"],
    [134, "2m 14s"],
    [120, "2m"],
    [3900, "1h 5m"],
    [7200, "2h"],
  ])("%d seconds is %s", (seconds, text) => {
    expect(formatElapsed(seconds)).toBe(text);
  });
});

describe("formatTime", () => {
  it.each([
    ["2026-11-02T08:00:00+00:00", "Today 08:00"],
    ["2026-11-01T21:14:00Z", "Yesterday 21:14"],
    ["2026-11-03T06:00:00Z", "Tomorrow 06:00"],
    ["2026-10-29T08:00:00Z", "Thu 08:00"],
    ["2026-10-03T08:00:00Z", "3 Oct 08:00"],
    ["2025-10-03T08:00:00Z", "3 Oct 2025"],
  ])("%s reads %s", (iso, text) => {
    expect(formatTime(iso, NOW, "en-GB")).toBe(text);
  });

  it("leaves something unparseable as it is", () => {
    expect(formatTime("soon", NOW)).toBe("soon");
  });
});

describe("formatDate", () => {
  it("is the date alone", () => {
    expect(formatDate("2024-01-01T00:00:00Z", "en-GB")).toBe("1 Jan 2024");
  });

  it("keeps a plain date on its calendar day west of Greenwich", () => {
    const tz = process.env.TZ;
    process.env.TZ = "America/New_York";
    try {
      expect(formatDate("2014-01-28", "en-GB")).toBe("28 Jan 2014");
    } finally {
      process.env.TZ = tz;
    }
  });
});

describe("formatRelative", () => {
  it.each([
    ["2026-11-02T20:10:00Z", "in 5h 40m"],
    ["2026-11-02T14:18:00Z", "12 min ago"],
    ["2026-11-02T14:29:50Z", "just now"],
    ["2026-11-02T14:30:20Z", "in a moment"],
    ["2026-11-02T16:30:00Z", "in 2h"],
    ["2026-10-31T14:30:00Z", "2 days ago"],
  ])("%s is %s", (iso, text) => {
    expect(formatRelative(iso, NOW)).toBe(text);
  });
});

describe("plural", () => {
  it("names one and many", () => {
    expect(plural(1, "book")).toBe("1 book");
    expect(plural(3, "book")).toBe("3 books");
    expect(plural(2, "library", "libraries")).toBe("2 libraries");
  });
});
