/**
 * The Settings form as text and back: the draft the inputs edit, which fields differ
 * from what is saved, the partial `PUT /api/settings` body, and which field a refusal
 * belongs to. Pure, so the rules are tested without rendering the page.
 */
import type { components } from "../../api/schema";
import { templateError } from "./naming";

export type Settings = components["schemas"]["SettingsOut"];
export type SettingsUpdate = components["schemas"]["SettingsUpdate"];

export type Unit = "hours" | "minutes";

/** What the inputs hold: numbers as typed, so a half-typed value is not lost. */
export interface Draft {
  sync_enabled: boolean;
  interval: string;
  unit: Unit;
  auto_monitor_new: boolean;
  max_download: string;
  max_attempts: string;
  max_consecutive_failures: string;
  encoding_format: string;
  bitrate: number;
  audiobook_folder: string;
  download_folder: string;
  folder_template: string;
  filename_template: string;
  webhook_url: string;
}

export type Field = keyof Draft;
/** The fields an error is shown under: the interval's number and unit are one */
export type ErrorField = Exclude<Field, "unit">;
export type Errors = Partial<Record<ErrorField | "form", string>>;

const optional = (value: number | null) => (value == null ? "" : String(value));

export function draftFrom(settings: Settings): Draft {
  const minutes = settings.sync_interval_minutes;
  const inHours = minutes % 60 === 0;
  return {
    sync_enabled: settings.sync_enabled,
    interval: String(inHours ? minutes / 60 : minutes),
    unit: inHours ? "hours" : "minutes",
    auto_monitor_new: settings.auto_monitor_new,
    max_download: optional(settings.max_download),
    max_attempts: String(settings.max_attempts),
    max_consecutive_failures: optional(settings.max_consecutive_failures),
    encoding_format: settings.encoding_format,
    bitrate: settings.bitrate,
    audiobook_folder: settings.audiobook_folder,
    download_folder: settings.download_folder,
    folder_template: settings.folder_template,
    filename_template: settings.filename_template,
    webhook_url: settings.webhook_url ?? "",
  };
}

/** A whole number as typed, or NaN; `""` is null. */
function whole(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  return /^\d+$/.test(trimmed) ? Number(trimmed) : Number.NaN;
}

/** The interval in minutes as the draft has it: NaN while it is not a whole number. */
export function intervalMinutes(draft: Pick<Draft, "interval" | "unit">): number {
  const value = whole(draft.interval);
  if (value == null) return Number.NaN;
  return draft.unit === "hours" ? value * 60 : value;
}

/**
 * The other unit, keeping the interval: 6 hours becomes 360 minutes; minutes become
 * the nearest whole hour, at least one, since the setting is whole minutes.
 */
export function switchUnit(draft: Pick<Draft, "interval" | "unit">, unit: Unit): Pick<Draft, "interval" | "unit"> {
  if (unit === draft.unit) return draft;
  const value = whole(draft.interval);
  if (value == null || Number.isNaN(value)) return { interval: draft.interval, unit };
  const converted = unit === "minutes" ? value * 60 : Math.max(1, Math.round(value / 60));
  return { interval: String(converted), unit };
}

/** The setting each draft field stands for, as `PUT /api/settings` takes it. */
type Values = { [K in keyof SettingsUpdate]-?: SettingsUpdate[K] };

/** The draft as settings, with what cannot be sent yet as a field error. */
function parse(draft: Draft): { values: Partial<Values>; errors: Errors } {
  const errors: Errors = {};
  const values: Partial<Values> = {
    sync_enabled: draft.sync_enabled,
    auto_monitor_new: draft.auto_monitor_new,
    encoding_format: draft.encoding_format,
    bitrate: draft.bitrate,
    audiobook_folder: draft.audiobook_folder.trim(),
    download_folder: draft.download_folder.trim(),
    folder_template: draft.folder_template,
    filename_template: draft.filename_template,
    webhook_url: draft.webhook_url.trim() || null,
  };

  const minutes = intervalMinutes(draft);
  if (Number.isNaN(minutes)) errors.interval = "Enter a whole number.";
  else values.sync_interval_minutes = minutes;

  for (const key of ["max_download", "max_consecutive_failures"] as const) {
    const value = whole(draft[key]);
    if (Number.isNaN(value)) errors[key] = "Enter a whole number, or leave it empty.";
    else values[key] = value;
  }
  const attempts = whole(draft.max_attempts);
  if (attempts == null || Number.isNaN(attempts)) errors.max_attempts = "Enter a whole number.";
  else values.max_attempts = attempts;

  if (!values.audiobook_folder) errors.audiobook_folder = "Enter a folder.";
  if (!values.download_folder) errors.download_folder = "Enter a folder.";
  const naming = templateError(draft.folder_template, draft.filename_template);
  if (naming) errors[templateErrorField(naming, draft)] = naming;

  return { values, errors };
}

/** Which draft fields hold a setting the update would change. */
const SETTING_OF: Record<ErrorField, keyof Values> = {
  sync_enabled: "sync_enabled",
  interval: "sync_interval_minutes",
  auto_monitor_new: "auto_monitor_new",
  max_download: "max_download",
  max_attempts: "max_attempts",
  max_consecutive_failures: "max_consecutive_failures",
  encoding_format: "encoding_format",
  bitrate: "bitrate",
  audiobook_folder: "audiobook_folder",
  download_folder: "download_folder",
  folder_template: "folder_template",
  filename_template: "filename_template",
  webhook_url: "webhook_url",
};

/** The fields that differ from what is saved: the interval once, whatever its unit. */
export function changedFields(draft: Draft, saved: Draft): ErrorField[] {
  return (Object.keys(SETTING_OF) as ErrorField[]).filter((field) =>
    field === "interval"
      ? intervalMinutes(draft) !== intervalMinutes(saved)
      : // Text compared as it would be sent: trailing spaces on a folder are not a change
        normalise(field, draft[field]) !== normalise(field, saved[field]),
  );
}

function normalise(field: ErrorField, value: Draft[ErrorField]): Draft[ErrorField] {
  return typeof value === "string" && ["audiobook_folder", "download_folder", "webhook_url"].includes(field)
    ? value.trim()
    : value;
}

/**
 * The body for `PUT /api/settings`: only the changed fields, an empty optional number
 * or webhook as `null`. Errors for any changed field that cannot be sent as typed.
 */
export function updateFrom(draft: Draft, saved: Draft): { update: SettingsUpdate; errors: Errors } {
  const { values, errors } = parse(draft);
  const update: SettingsUpdate = {};
  const shown: Errors = {};
  for (const field of changedFields(draft, saved)) {
    if (errors[field]) shown[field] = errors[field];
    else Object.assign(update, { [SETTING_OF[field]]: values[SETTING_OF[field]] });
  }
  // The saved templates are valid, so a template error is always this draft's - even
  // when it is pinned on the template that did not change (the two are identical)
  for (const field of ["folder_template", "filename_template"] as const) {
    if (errors[field]) shown[field] = errors[field];
  }
  return { update, errors: shown };
}

/** The template a naming error belongs to: the one its message quotes, else the folder's. */
export function templateErrorField(
  message: string,
  draft: Pick<Draft, "folder_template" | "filename_template">,
): "folder_template" | "filename_template" {
  if (message.startsWith("naming filename template")) return "filename_template";
  const template = /in naming template '(.*)'\. Valid placeholders/s.exec(message)?.[1];
  if (template !== undefined && template === draft.filename_template && template !== draft.folder_template) {
    return "filename_template";
  }
  return "folder_template";
}

// The settings validators' messages begin with the config.ini section and key
const PREFIXES: [string, ErrorField][] = [
  ["sync max-download", "max_download"],
  ["sync max-attempts", "max_attempts"],
  ["sync max-consecutive-failures", "max_consecutive_failures"],
  ["sync interval-minutes", "interval"],
  ["notifications webhook-url", "webhook_url"],
  ["encoding bitrate", "bitrate"],
  ["encoding format", "encoding_format"],
];

/**
 * The field a `422` from `PUT /api/settings` belongs to, from the validator's message;
 * `form` for one that names none, shown beside Save. With one field sent, it is that one.
 */
export function fieldOfError(detail: string, draft: Draft, sent: ErrorField[]): ErrorField | "form" {
  for (const [prefix, field] of PREFIXES) if (detail.startsWith(prefix)) return field;
  if (detail.startsWith("naming filename template") || detail.startsWith("Unknown placeholder")) {
    return templateErrorField(detail, draft);
  }
  return sent.length === 1 && sent[0] ? sent[0] : "form";
}
