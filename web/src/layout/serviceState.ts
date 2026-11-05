import type { Status } from "../api/queries";

export type ServiceState = { text: string; colour: string };

/**
 * The sidebar's one line about the service: healthy, or what the run in flight is
 * doing ("Syncing · book 3 of 10"), or that it is not answering.
 */
export function serviceState(status: Status | undefined, failed: boolean): ServiceState {
  if (failed) {
    return { text: "Service not answering", colour: "var(--bad)" };
  }
  if (!status) {
    return { text: "Checking the service…", colour: "var(--faint)" };
  }
  const run = status.current_run;
  if (run) {
    if (run.stage === "cancelling") {
      return { text: "Cancelling the run", colour: "var(--accent)" };
    }
    if (run.stage === "downloading" && run.books_total > 0) {
      // books_done counts the slots used; the book in hand is the next one
      const book = Math.min(run.books_done + 1, run.books_total);
      return { text: `Syncing · book ${book} of ${run.books_total}`, colour: "var(--accent)" };
    }
    return { text: "Syncing the library", colour: "var(--accent)" };
  }
  if (status.scheduler.running) {
    return { text: "Syncing", colour: "var(--accent)" };
  }
  return { text: "Service healthy", colour: "var(--ok)" };
}

/** Whether any account has to be signed in to Audible again: the Accounts link's dot. */
export function accountsNeedLogin(status: Status | undefined): boolean {
  return status?.accounts.some((account) => account.needs_login) ?? false;
}
