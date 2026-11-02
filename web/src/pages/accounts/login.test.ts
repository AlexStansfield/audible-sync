import { describe, expect, it } from "vitest";
import { account } from "../../test/fixtures";
import {
  accountDetail,
  accountState,
  defaultMarketplace,
  doneText,
  type Login,
  remainingText,
  removeText,
  shortAddress,
  stepIndex,
} from "./login";

const NOW = Date.parse("2026-11-02T12:00:00Z");
const MARKETPLACES = [
  { country_code: "de", domain: "de", name: "Germany" },
  { country_code: "us", domain: "com", name: "United States" },
  { country_code: "uk", domain: "co.uk", name: "United Kingdom" },
  { country_code: "ca", domain: "ca", name: "Canada" },
];

function login(overrides: Partial<Login> = {}): Login {
  return {
    login_id: "abc",
    method: "code",
    status: "pending",
    expires_at: "2026-11-02T12:10:00+00:00",
    code: "KQ7XM",
    verification_url: "https://www.amazon.co.uk/code",
    ...overrides,
  };
}

describe("the sign-in dialog's rules", () => {
  it("counts down in minutes and seconds, never below zero", () => {
    expect(remainingText("2026-11-02T12:09:12+00:00", NOW)).toBe("9:12 left");
    expect(remainingText("2026-11-02T12:00:00.500Z", NOW)).toBe("0:01 left");
    expect(remainingText("2026-11-02T11:59:00+00:00", NOW)).toBe("0:00 left");
  });

  it("shows Amazon's address without the scheme", () => {
    expect(shortAddress("https://www.amazon.co.uk/code")).toBe("amazon.co.uk/code");
  });

  it("starts on the browser's own region when Audible has one", () => {
    expect(defaultMarketplace(MARKETPLACES, ["en-GB", "en"])).toBe("uk");
    expect(defaultMarketplace(MARKETPLACES, ["fr-CA"])).toBe("ca");
    expect(defaultMarketplace(MARKETPLACES, ["en", "nl-NL"])).toBe("de");
    expect(defaultMarketplace([], ["en-GB"])).toBe("");
  });

  it("knows how far along each stage is", () => {
    expect(stepIndex({ step: "setup" })).toBe(0);
    expect(stepIndex({ step: "code", login: login() })).toBe(1);
    expect(stepIndex({ step: "done", account: null })).toBe(2);
  });

  it("words the done screen for a new account and for a sign-in again", () => {
    const de = account({ name: "Alex (DE)" });
    expect(doneText({ kind: "add" }, de).title).toBe("Alex (DE) is connected");
    expect(doneText({ kind: "relogin", account: account() }, null)).toEqual({
      title: "Alex (UK) is signed in again",
      text: "The library and history are unchanged. Refused books are queued for the next run.",
    });
  });
});

describe("the account cards' wording", () => {
  it("gives each account its state", () => {
    expect(accountState(account()).label).toBe("Active");
    expect(accountState(account({ enabled: false })).label).toBe("Paused");
    expect(accountState(account({ enabled: false, needs_login: true })).label).toBe("Needs sign-in");
  });

  it("says who, where and since when", () => {
    const uk = MARKETPLACES[2];
    expect(accountDetail(account(), uk, () => "12 Sep 2026")).toBe("Alex · audible.co.uk · added 12 Sep 2026");
    expect(accountDetail(account({ customer_name: null, created_at: null }), undefined, String)).toBe("UK");
    expect(accountDetail(account({ needs_login: true }), uk, String)).toMatch(/Not signed in/);
  });

  it("says what stays on disk when an account goes", () => {
    expect(removeText(41)).toContain("The 41 books already in your audiobooks folder stay on disk.");
    expect(removeText(1)).toContain("The book already in your audiobooks folder stays on disk.");
    expect(removeText(0)).toContain("Files already in your audiobooks folder stay on disk.");
    expect(removeText(undefined)).toContain("Files already");
  });
});
