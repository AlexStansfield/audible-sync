/** Settings' wording: the unsaved count, the next run, the disk, FFmpeg and the key. */
import type { components } from "../../api/schema";
import { formatBytes, formatClock, plural } from "../../lib/format";

type Disk = components["schemas"]["DiskOut"];
type SystemInfo = components["schemas"]["SystemInfo"];
type Stats = components["schemas"]["Stats"];
type Scheduler = components["schemas"]["SchedulerStatus"];

export function unsavedText(count: number): string {
  return plural(count, "unsaved change");
}

/**
 * The line beside "Run every": when the next run is, from the saved schedule - an
 * edited interval or switch moves it only once saved, so say that instead.
 */
export function scheduleText(scheduler: Scheduler | undefined, edited: boolean, now: Date = new Date()): string {
  if (edited) return "The next run is worked out again when you save. At least 5 minutes.";
  if (!scheduler) return "At least 5 minutes.";
  if (!scheduler.enabled || !scheduler.next_run_at) return "No scheduled runs. At least 5 minutes.";
  if (Date.parse(scheduler.next_run_at) <= now.getTime()) return "Next run due now. At least 5 minutes.";
  return `Next run ${formatClock(scheduler.next_run_at, now)}. At least 5 minutes.`;
}

/** The average downloaded book's size on disk, or null before there is one. */
export function averageBookBytes(stats: Stats | undefined): number | null {
  const downloaded = stats?.books.by_status.downloaded ?? 0;
  if (!stats || downloaded === 0 || stats.bytes_on_disk <= 0) return null;
  return stats.bytes_on_disk / downloaded;
}

/** `1.6 TB free of 4 TB` */
export function diskFree(disk: Disk): string {
  if (disk.free_bytes == null || disk.total_bytes == null) return "Free space unknown";
  return `${formatBytes(disk.free_bytes)} free of ${formatBytes(disk.total_bytes)}`;
}

/** How full the disk is, 0-100, or null when the sizes could not be read. */
export function diskUsedPercent(disk: Disk): number | null {
  if (disk.free_bytes == null || !disk.total_bytes) return null;
  return Math.round(((disk.total_bytes - disk.free_bytes) / disk.total_bytes) * 100);
}

export function diskNote(disk: Disk, average: number | null): string {
  const fit = average && disk.free_bytes != null ? Math.floor(disk.free_bytes / average).toLocaleString("en-GB") : null;
  if (disk.low_space) {
    return fit
      ? `Space is low. About ${fit} more books fit at your average size; downloads will fail when the disk is full.`
      : "Space is low: downloads will fail when the disk is full.";
  }
  if (disk.free_bytes == null) return "The service could not read this folder's disk.";
  return fit && average ? `About ${fit} more books fit at your average size of ${formatBytes(average)}.` : "";
}

/** The FFmpeg line: its version and whether libopus is there for Ogg Opus. */
export function ffmpegText(system: SystemInfo): { version: string; note: string; problem: boolean } {
  if (!system.ffmpeg_version) return { version: "Not found", note: "books cannot be decrypted", problem: true };
  // `ffmpeg version 7.1.1 Copyright ...`: the version alone
  const version = /^ffmpeg version (\S+)/.exec(system.ffmpeg_version)?.[1] ?? system.ffmpeg_version;
  return system.libopus
    ? { version, note: "libopus available", problem: false }
    : { version, note: "no libopus: Ogg Opus will fail", problem: true };
}
