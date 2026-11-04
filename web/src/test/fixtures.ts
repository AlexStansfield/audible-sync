/** Typed sample data for screen tests: a book, an account, a run, the stats. */
import type { components } from "../api/schema";

type S = components["schemas"];
export type Book = S["BookOut"];
export type Account = S["AccountOut"];
export type SyncRun = S["SyncRunOut"];

let nextId = 1;

export function book(overrides: Partial<Book> = {}): Book {
  const id = overrides.id ?? nextId++;
  return {
    id,
    account_id: 1,
    asin: `B0${String(id).padStart(8, "0")}`,
    title: `Book ${id}`,
    subtitle: "",
    authors: ["Pierce Brown"],
    narrators: ["Tim Gerard Reynolds"],
    series: [],
    primary_series: null,
    genres: [],
    length: 960,
    is_finished: false,
    percent_complete: 0,
    date_added: "2026-10-01T00:00:00Z",
    release_date: "2014-01-28",
    cover_url: "https://example.invalid/cover.jpg",
    has_pdf: false,
    is_consumable: true,
    status: "waiting_download",
    monitored: true,
    attempts: 0,
    last_error: null,
    last_attempt_at: null,
    file_path: null,
    pdf_path: null,
    cover_path: null,
    annotations_path: null,
    encoding_format: null,
    downloaded_at: null,
    ...overrides,
  };
}

export function account(overrides: Partial<Account> = {}): Account {
  return {
    id: 1,
    name: "Alex (UK)",
    country_code: "uk",
    customer_name: "Alex",
    enabled: true,
    monitor_existing: true,
    needs_login: false,
    created_at: "2026-09-12T10:00:00+00:00",
    last_synced_at: "2026-11-02T08:00:00+00:00",
    ...overrides,
  };
}

export function run(overrides: Partial<SyncRun> = {}): SyncRun {
  return {
    id: 148,
    account_id: 1,
    started_at: "2026-11-02T08:00:00+00:00",
    finished_at: "2026-11-02T08:12:00+00:00",
    outcome: "success",
    books_seen: 2,
    books_added: 2,
    books_downloaded: 2,
    books_failed: 0,
    error: null,
    books_recorded: true,
    ...overrides,
  };
}

/**
 * A stats `BookCounts`. Every book is monitored and every unavailable one retryable
 * unless `extra` says otherwise (`unmonitored` a number of the waiting books, or
 * `monitored_by_status` in full; `retryable_unavailable`).
 */
export function counts(
  byStatus: Partial<Record<string, number>>,
  unmonitored = 0,
  extra: Partial<Pick<S["BookCounts"], "monitored_by_status" | "retryable_unavailable">> = {},
): S["BookCounts"] {
  const by_status = { waiting_download: 0, downloading: 0, downloaded: 0, unavailable: 0, failed: 0, ...byStatus };
  const total = Object.values(by_status).reduce((sum, n) => sum + (n ?? 0), 0);
  const monitored_by_status = extra.monitored_by_status ?? {
    ...by_status,
    waiting_download: by_status.waiting_download - unmonitored,
  };
  const monitored = Object.values(monitored_by_status).reduce((sum, n) => sum + n, 0);
  return {
    by_status: by_status as Record<string, number>,
    monitored_by_status,
    total,
    monitored,
    unmonitored: total - monitored,
    retryable_unavailable: extra.retryable_unavailable ?? by_status.unavailable,
  };
}

export function settings(overrides: Partial<S["SettingsOut"]> = {}): S["SettingsOut"] {
  return {
    debug: false,
    sync_enabled: true,
    sync_interval_minutes: 360,
    max_download: 20,
    max_attempts: 3,
    max_consecutive_failures: null,
    auto_monitor_new: true,
    download_folder: "/app/data/downloads",
    audiobook_folder: "/audiobooks",
    folder_template: "{author}/[{series}/][{sequence} - ]{title}",
    filename_template: "{title}",
    encoding_format: "m4b",
    bitrate: 64,
    webhook_url: null,
    ...overrides,
  };
}

export function system(overrides: Partial<S["SystemInfo"]> = {}): S["SystemInfo"] {
  return {
    version: "2.0.0",
    ffmpeg_version: "ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers",
    libopus: true,
    audiobook_folder: { path: "/audiobooks", free_bytes: 1.6e12, total_bytes: 4e12, low_space: false },
    key_source: "file",
    key_file: "/app/secrets/secret.key",
    database_bytes: 4.2e6,
    ...overrides,
  };
}
