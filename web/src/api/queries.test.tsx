import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { status } from "../test/fakeApi";
import { ApiError, NetworkError } from "./client";
import { IDLE_INTERVAL, RUNNING_INTERVAL, statusInterval, useRefreshAfterRun } from "./queries";
import { makeQueryClient, shouldRetry } from "./queryClient";

describe("statusInterval", () => {
  it("polls every 2 s while a run is in flight and every 10 s otherwise", () => {
    expect(statusInterval(undefined)).toBe(IDLE_INTERVAL);
    expect(statusInterval(status())).toBe(IDLE_INTERVAL);
    const running = status({ scheduler: { ...status().scheduler, running: true } });
    expect(statusInterval(running)).toBe(RUNNING_INTERVAL);
  });
});

describe("useRefreshAfterRun", () => {
  function setup(running: boolean | undefined) {
    const client = makeQueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const hook = renderHook(({ value }) => useRefreshAfterRun(value), { wrapper, initialProps: { value: running } });
    const refreshed = () => invalidate.mock.calls.map(([filters]) => filters?.queryKey?.[0]);
    return { hook, refreshed };
  }

  it("refetches what a run changes when it ends", () => {
    const { hook, refreshed } = setup(true);

    hook.rerender({ value: false });

    expect(refreshed()).toEqual(["stats", "books", "runs", "queue", "accounts", "wishlist", "catalog"]);
  });

  it("does nothing while idle, or when a run starts", () => {
    const { hook, refreshed } = setup(undefined);

    hook.rerender({ value: false });
    hook.rerender({ value: true });

    expect(refreshed()).toEqual([]);
  });
});

describe("shouldRetry", () => {
  it("retries a server error once, never a 4xx or a lost service", () => {
    expect(shouldRetry(0, new ApiError(500, "boom", "GET", "/api/status"))).toBe(true);
    expect(shouldRetry(1, new ApiError(500, "boom", "GET", "/api/status"))).toBe(false);
    expect(shouldRetry(0, new ApiError(404, "gone", "GET", "/api/books/1"))).toBe(false);
    expect(shouldRetry(0, new NetworkError("GET", "/api/status", null))).toBe(false);
  });
});
