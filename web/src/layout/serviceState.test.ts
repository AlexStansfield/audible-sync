import { describe, expect, it } from "vitest";
import type { Status } from "../api/queries";
import { status } from "../test/fakeApi";
import { accountsNeedLogin, serviceState } from "./serviceState";

type Run = NonNullable<Status["current_run"]>;

function running(run: Partial<Run>): Status {
  return status({
    scheduler: { ...status().scheduler, running: true },
    current_run: {
      run_id: 148,
      started_at: "2026-11-02T14:00:00+00:00",
      stage: "downloading",
      account: null,
      book: null,
      full_refresh: false,
      books_done: 2,
      books_total: 10,
      transfer: null,
      ...run,
    },
  });
}

describe("serviceState", () => {
  it("is healthy between runs", () => {
    expect(serviceState(status(), false).text).toBe("Service healthy");
  });

  it("counts the book in hand during the downloads", () => {
    expect(serviceState(running({}), false).text).toBe("Syncing · book 3 of 10");
    expect(serviceState(running({ books_done: 10 }), false).text).toBe("Syncing · book 10 of 10");
  });

  it("says what a run is doing before and after the downloads", () => {
    expect(serviceState(running({ stage: "syncing", books_total: 0 }), false).text).toBe("Syncing the library");
    expect(serviceState(running({ stage: "cancelling" }), false).text).toBe("Cancelling the run");
    expect(serviceState(running({ stage: "syncing", full_refresh: true, books_total: 0 }), false).text).toBe(
      "Refreshing every book",
    );
    // A full refresh downloads like any other run
    expect(serviceState(running({ full_refresh: true }), false).text).toBe("Syncing · book 3 of 10");
  });

  it("is not answering when the status fails, and checking before the first answer", () => {
    expect(serviceState(undefined, true).text).toBe("Service not answering");
    expect(serviceState(undefined, false).text).toBe("Checking the service…");
  });
});

describe("accountsNeedLogin", () => {
  const account = {
    id: 1,
    name: "Alex (UK)",
    country_code: "uk",
    customer_name: "Alex",
    enabled: true,
    monitor_existing: true,
    needs_login: false,
    created_at: null,
    last_synced_at: null,
  };

  it("is true when any account needs a sign-in", () => {
    expect(accountsNeedLogin(status({ accounts: [account] }))).toBe(false);
    expect(accountsNeedLogin(status({ accounts: [account, { ...account, id: 2, needs_login: true }] }))).toBe(true);
    expect(accountsNeedLogin(undefined)).toBe(false);
  });
});
