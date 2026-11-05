/**
 * The theme choice: System (follows `prefers-color-scheme`), Light or Dark, kept per
 * browser in localStorage. The inline script in index.html applies the same rule
 * before the first paint; keep the key and the values in step with it.
 */

export const THEME_KEY = "audible-sync.theme";

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

export function readThemeChoice(storage: Pick<Storage, "getItem"> | null = safeStorage()): ThemeChoice {
  const stored = storage?.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

export function resolveTheme(choice: ThemeChoice, prefersLight: boolean): Theme {
  if (choice === "system") return prefersLight ? "light" : "dark";
  return choice;
}

export function systemPrefersLight(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches;
}

/** Store the choice (nothing stored is System) and apply it to <html>. */
export function applyThemeChoice(choice: ThemeChoice, storage: Storage | null = safeStorage()): Theme {
  try {
    if (choice === "system") storage?.removeItem(THEME_KEY);
    else storage?.setItem(THEME_KEY, choice);
  } catch {
    // Storage refused (private mode, quota): the theme still applies for this page
  }
  const theme = resolveTheme(choice, systemPrefersLight());
  document.documentElement.dataset.theme = theme;
  return theme;
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}
