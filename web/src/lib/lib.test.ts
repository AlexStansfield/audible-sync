import { describe, expect, it } from "vitest";
import { loginPath, safeNext } from "./next";
import { passwordStrength } from "./password";
import { applyThemeChoice, readThemeChoice, resolveTheme, THEME_KEY } from "./theme";

describe("safeNext", () => {
  it.each([
    ["/library?status=failed", "/library?status=failed"],
    ["/library/12", "/library/12"],
    [null, "/"],
    ["", "/"],
    ["library", "/"],
    ["//evil.example/x", "/"],
    ["/\\evil.example", "/"],
    ["https://evil.example/", "/"],
    ["/login", "/"],
    ["/login?next=%2Flibrary", "/"],
  ])("%j goes to %s", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });
});

describe("loginPath", () => {
  it("returns to the page you were on", () => {
    expect(loginPath("/library?status=failed")).toBe("/login?next=%2Flibrary%3Fstatus%3Dfailed");
  });

  it("needs no next for the dashboard", () => {
    expect(loginPath("/")).toBe("/login");
  });
});

describe("passwordStrength", () => {
  it.each([
    ["", 0],
    ["short", 1],
    ["aaaaaaaaaaaaaaaa", 1],
    ["123456789012345", 1],
    ["correcthorsebat", 2],
    ["correct horse battery", 3],
    ["Correct-horse-9", 3],
    ["Correct horse battery staple 9", 4],
  ])("%j scores %d", (password, score) => {
    expect(passwordStrength(password)).toBe(score);
  });
});

describe("theme", () => {
  it("reads a stored choice, and anything else as System", () => {
    expect(readThemeChoice({ getItem: () => "light" })).toBe("light");
    expect(readThemeChoice({ getItem: () => "dark" })).toBe("dark");
    expect(readThemeChoice({ getItem: () => "purple" })).toBe("system");
    expect(readThemeChoice({ getItem: () => null })).toBe("system");
  });

  it("follows the system only under System", () => {
    expect(resolveTheme("system", true)).toBe("light");
    expect(resolveTheme("system", false)).toBe("dark");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("stores the choice, keeps nothing for System, and sets <html data-theme>", () => {
    expect(applyThemeChoice("light")).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");

    // jsdom's stand-in matchMedia prefers nothing, so System is dark
    expect(applyThemeChoice("system")).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
