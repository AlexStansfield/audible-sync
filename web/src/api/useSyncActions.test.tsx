import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../components/Toasts";
import { fail, fakeApi, ok } from "../test/fakeApi";
import { makeQueryClient } from "./queryClient";
import { useSyncActions } from "./useSyncActions";

function setup() {
  const client = makeQueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
  const { result } = renderHook(() => useSyncActions(), { wrapper });
  return { result, invalidate };
}

describe("useSyncActions", () => {
  it("starts a sync and refetches the status straight away", async () => {
    fakeApi({ "POST /api/sync": ok({ status: "started" }) });
    const { result, invalidate } = setup();

    await act(() => result.current.start.mutateAsync());

    expect(await screen.findByRole("status")).toHaveTextContent("Sync started.");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["status"] });
  });

  it("says so when a sync was already running, and still refetches", async () => {
    fakeApi({ "POST /api/sync": fail(409, "A sync is already running") });
    const { result, invalidate } = setup();

    await act(() => result.current.start.mutateAsync().catch(() => undefined));

    expect(await screen.findByRole("alert")).toHaveTextContent("A sync is already running");
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["status"] }));
  });

  it("says it is cancelling, promising nothing about the book in hand", async () => {
    fakeApi({ "POST /api/sync/cancel": ok({ status: "cancelling" }) });
    const { result } = setup();

    await act(() => result.current.cancel.mutateAsync());

    // A book being decrypted is finished first, so the toast does not say it goes back
    expect(await screen.findByRole("status")).toHaveTextContent(/^Cancelling the run\.$/);
  });

  it("cancels, or says there was nothing to cancel", async () => {
    fakeApi({ "POST /api/sync/cancel": fail(409, "No sync is running") });
    const { result } = setup();

    await act(() => result.current.cancel.mutateAsync().catch(() => undefined));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No run to cancel");
    expect(alert).toHaveTextContent("No sync is running");
  });
});
