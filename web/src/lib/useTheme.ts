import { useCallback, useEffect, useState } from "react";
import { applyThemeChoice, readThemeChoice, type ThemeChoice } from "./theme";

/** The theme choice and a setter; under System it follows the OS as it changes. */
export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(() => readThemeChoice());

  useEffect(() => {
    applyThemeChoice(choice);
    if (choice !== "system" || typeof matchMedia !== "function") return;
    const query = matchMedia("(prefers-color-scheme: light)");
    const follow = () => applyThemeChoice("system");
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, [choice]);

  return [choice, useCallback((next: ThemeChoice) => setChoice(next), [])];
}
