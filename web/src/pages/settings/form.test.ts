import { describe, expect, it } from "vitest";
import { settings } from "../../test/fixtures";
import { changedFields, draftFrom, fieldOfError, switchUnit, updateFrom } from "./form";

const SETTINGS = settings();
const SAVED = draftFrom(SETTINGS);

describe("the draft", () => {
  it("shows an interval in whole hours as hours, anything else in minutes", () => {
    expect(SAVED).toMatchObject({ interval: "6", unit: "hours" });
    expect(draftFrom({ ...SETTINGS, sync_interval_minutes: 90 })).toMatchObject({ interval: "90", unit: "minutes" });
  });

  it("reads an unset limit or webhook as an empty field", () => {
    expect(SAVED).toMatchObject({ max_download: "20", max_consecutive_failures: "", webhook_url: "" });
  });

  it("keeps the interval when the unit changes", () => {
    expect(switchUnit({ interval: "6", unit: "hours" }, "minutes")).toEqual({ interval: "360", unit: "minutes" });
    expect(switchUnit({ interval: "100", unit: "minutes" }, "hours")).toEqual({ interval: "2", unit: "hours" });
    expect(switchUnit({ interval: "5", unit: "minutes" }, "hours")).toEqual({ interval: "1", unit: "hours" });
    expect(switchUnit({ interval: "", unit: "minutes" }, "hours")).toEqual({ interval: "", unit: "hours" });
  });
});

describe("what changed", () => {
  it("is nothing for the same interval in other units, or a folder with spaces round it", () => {
    expect(
      changedFields({ ...SAVED, interval: "360", unit: "minutes", audiobook_folder: " /audiobooks " }, SAVED),
    ).toEqual([]);
  });

  it("counts each field once", () => {
    const oga = { ...SAVED, encoding_format: "oga" };
    expect(changedFields({ ...oga, interval: "5", unit: "minutes", bitrate: 48, debug: true } as never, oga)).toEqual([
      "interval",
      "bitrate",
    ]);
  });

  it("counts the bitrate only while Ogg Opus is chosen", () => {
    expect(changedFields({ ...SAVED, encoding_format: "m4b", bitrate: 32 }, SAVED)).toEqual([]);
    expect(changedFields({ ...SAVED, encoding_format: "oga", bitrate: 32 }, SAVED)).toEqual([
      "encoding_format",
      "bitrate",
    ]);
  });
});

describe("the update", () => {
  it("sends only what changed, an emptied limit as null", () => {
    const { update, errors } = updateFrom({ ...SAVED, max_download: "", interval: "30", unit: "minutes" }, SAVED);
    expect(errors).toEqual({});
    expect(update).toEqual({ max_download: null, sync_interval_minutes: 30 });
  });

  it("trims a webhook, and an emptied one is null", () => {
    expect(updateFrom({ ...SAVED, webhook_url: " http://abs:13378/hook " }, SAVED).update).toEqual({
      webhook_url: "http://abs:13378/hook",
    });
    const withHook = draftFrom({ ...SETTINGS, webhook_url: "http://abs" });
    expect(updateFrom({ ...withHook, webhook_url: "" }, withHook).update).toEqual({ webhook_url: null });
  });

  it("refuses what is not a whole number, or an empty attempts or folder", () => {
    const { update, errors } = updateFrom(
      { ...SAVED, max_download: "2.5", max_attempts: "", interval: "x", download_folder: " " },
      SAVED,
    );
    expect(update).toEqual({});
    expect(errors).toEqual({
      max_download: "Enter a whole number, or leave it empty.",
      max_attempts: "Enter a whole number.",
      interval: "Enter a whole number.",
      download_folder: "Enter a folder.",
    });
  });

  it("refuses a template the service would, under the template that holds the mistake", () => {
    expect(updateFrom({ ...SAVED, filename_template: "{title} {nope}" }, SAVED).errors).toEqual({
      filename_template: expect.stringContaining("Unknown placeholder '{nope}'"),
    });
    expect(updateFrom({ ...SAVED, filename_template: "{author}/{title}" }, SAVED).errors).toEqual({
      filename_template: "naming filename template '{author}/{title}' must not contain '/' or '\\'",
    });
    expect(updateFrom({ ...SAVED, folder_template: "{writer}/{title}" }, SAVED).errors).toEqual({
      folder_template: expect.stringContaining("Unknown placeholder '{writer}'"),
    });
  });
});

describe("which field a refusal belongs to", () => {
  it.each([
    ["sync max-download must be 1 or more, got 0", "max_download"],
    ["sync max-attempts must be 1 or more, got 0", "max_attempts"],
    ["sync max-consecutive-failures must be 1 or more, got 0", "max_consecutive_failures"],
    ["sync interval-minutes must be 5 or more, got 2", "interval"],
    ["notifications webhook-url must start with http:// or https://, got 'x'", "webhook_url"],
    ["encoding bitrate 300 must be between 1 and 256 kbps", "bitrate"],
    ["encoding format 'mp3' is not supported, use one of: m4b, oga", "encoding_format"],
    ["naming filename template 'a/b' must not contain '/' or '\\'", "filename_template"],
  ])("%s", (detail, field) => {
    expect(fieldOfError(detail, SAVED, ["max_download", "interval"])).toBe(field);
  });

  it("finds an unknown placeholder in the template that has it", () => {
    const draft = { ...SAVED, filename_template: "{nope}" };
    const detail = "Unknown placeholder '{nope}' in naming template '{nope}'. Valid placeholders: {asin}";
    expect(fieldOfError(detail, draft, [])).toBe("filename_template");
    expect(fieldOfError(detail.replaceAll("'{nope}'.", "'{nope}/x'."), draft, [])).toBe("folder_template");
  });

  it("is the one field sent, else the form", () => {
    expect(fieldOfError("Something else", SAVED, ["audiobook_folder"])).toBe("audiobook_folder");
    expect(fieldOfError("Something else", SAVED, ["audiobook_folder", "bitrate"])).toBe("form");
  });
});
