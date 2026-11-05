/**
 * The Accounts screen's wording and the rules of the sign-in dialog that need no React:
 * which step it is on, what a login's state means, the countdown, the default
 * marketplace.
 */
import type { components } from "../../api/schema";

type S = components["schemas"];
export type Account = S["AccountOut"];
export type Marketplace = S["MarketplaceOut"];
export type Login = S["LoginOut"];

/** Poll a code login this often (DESIGN.md: every 3 s). */
export const LOGIN_POLL = 3_000;

/** A new account, or new credentials for an existing one (Sign in again). */
export type Purpose = { kind: "add" } | { kind: "relogin"; account: Account };

/**
 * Where the dialog is. `code` and `browser` hold the login the service started; the
 * code step's pending, expired or failed is read from the login as polled.
 */
export type Stage =
  | { step: "setup" }
  | { step: "code"; login: Login }
  | { step: "browser"; login: Login }
  | { step: "done"; login: Login };

/** The progress bar's three steps, and how far along a stage is. */
export function stepLabels(purpose: Purpose): [string, string, string] {
  return purpose.kind === "relogin"
    ? ["Confirm account", "Sign in with Amazon", "Signed in"]
    : ["Choose marketplace", "Sign in with Amazon", "Connected"];
}

export function stepIndex(stage: Stage): number {
  return stage.step === "setup" ? 0 : stage.step === "done" ? 2 : 1;
}

export function dialogTitle(purpose: Purpose): string {
  return purpose.kind === "relogin" ? `Sign in again: ${purpose.account.name}` : "Add an Audible account";
}

/**
 * Whether a code login is still waiting, done, expired or failed: the server's word
 * alone. The countdown only shows the time; a browser whose clock runs ahead of the
 * service's would otherwise call a good code expired.
 */
export type CodeState = Login["status"];

/** `9:12 left`; never below `0:00`. */
export function remainingText(expiresAt: string, now: number): string {
  const seconds = Math.max(0, Math.ceil((Date.parse(expiresAt) - now) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")} left`;
}

/** `https://www.amazon.co.uk/code` → `amazon.co.uk/code`, as the dialog shows it. */
export function shortAddress(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/^www\./, "");
}

/** `United Kingdom (audible.co.uk)` */
export function marketplaceLabel(marketplace: Marketplace): string {
  return `${marketplace.name} (audible.${marketplace.domain})`;
}

/** A language Audible has one marketplace for, when the browser names no region. */
const LANGUAGE_MARKETPLACE: Record<string, string> = { de: "de", fr: "fr", it: "it", es: "es", ja: "jp" };

/**
 * The marketplace the add dialog starts on: the browser's own region when Audible has
 * one there (`en-GB` → `uk`, which is Audible's code for Great Britain; a script subtag
 * as in `pt-Latn-BR` is skipped), else the first language Audible has exactly one
 * marketplace for (`de` → `de`; English is too many), else the first.
 */
export function defaultMarketplace(marketplaces: readonly Marketplace[], languages: readonly string[]): string {
  const codes = new Set(marketplaces.map((marketplace) => marketplace.country_code));
  for (const language of languages) {
    const region = language
      .split("-")
      .slice(1)
      .find((part) => /^[a-z]{2}$/i.test(part))
      ?.toLowerCase();
    const code = region === "gb" ? "uk" : region;
    if (code && codes.has(code)) return code;
  }
  for (const language of languages) {
    const code = LANGUAGE_MARKETPLACE[language.split("-")[0]?.toLowerCase() ?? ""];
    if (code && codes.has(code)) return code;
  }
  return marketplaces[0]?.country_code ?? "";
}

type DoneLogin = Pick<Login, "account" | "replaced" | "requeued">;

/** Whether the account a sign-in landed on is paused: signing in does not undo that. */
export function isPaused(purpose: Purpose, login: DoneLogin): boolean {
  const account = login.account ?? (purpose.kind === "relogin" ? purpose.account : null);
  return account?.enabled === false;
}

/**
 * Only what the sign-in really moved: a title Audible withdrew stays parked, so often
 * nothing. Says where the books are, not when: `max_download` may not reach them next run.
 */
function requeuedText(requeued: number): string {
  if (requeued === 0) return "";
  return requeued === 1
    ? " 1 refused book is waiting to download again."
    : ` ${requeued.toLocaleString()} refused books are waiting to download again.`;
}

/**
 * The account a done screen names, and what it says next. Adding a login an account
 * already holds signs that account in again (`replaced`), which says why no new one came.
 */
export function doneText(purpose: Purpose, login: DoneLogin): { title: string; text: string } {
  const name = login.account?.name ?? (purpose.kind === "relogin" ? purpose.account.name : "The account");
  const paused = isPaused(purpose, login) ? " The account is paused, so runs skip it until you turn Sync back on." : "";
  const unchanged = `The library and history are unchanged.${requeuedText(login.requeued)}${paused}`;
  if (purpose.kind === "relogin") return { title: `${name} is signed in again`, text: unchanged };
  return login.replaced
    ? { title: `${name} is signed in again`, text: `This Audible login already had this account. ${unchanged}` }
    : { title: `${name} is connected`, text: "The library is fetched on the next run." };
}

/** An account card's chip: whether it can sync, and whether it will. */
export function accountState(account: Account): { label: string; colour: string } {
  if (account.needs_login) return { label: "Needs sign-in", colour: "var(--bad)" };
  return account.enabled ? { label: "Active", colour: "var(--ok)" } : { label: "Paused", colour: "var(--text-2)" };
}

/** The line under an account's name: who, where, since when - or what it needs. */
export function accountDetail(
  account: Account,
  marketplace: Marketplace | undefined,
  formatDate: (iso: string) => string,
): string {
  if (account.needs_login) return "Not signed in, so it is skipped. Sign in to Amazon to sync it.";
  return [
    account.customer_name,
    marketplace ? `audible.${marketplace.domain}` : account.country_code.toUpperCase(),
    account.created_at ? `added ${formatDate(account.created_at)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The remove dialog's sentence about what stays on disk. */
export function removeText(downloaded: number | undefined): string {
  const files =
    downloaded === undefined || downloaded === 0
      ? "Files already in your audiobooks folder stay on disk."
      : downloaded === 1
        ? "The book already in your audiobooks folder stays on disk."
        : `The ${downloaded} books already in your audiobooks folder stay on disk.`;
  return `Its library rows and run history are deleted from Audible Sync. ${files} You can add the account again later.`;
}
