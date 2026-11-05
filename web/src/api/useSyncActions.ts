import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/Toasts";
import { ApiError, api, unwrap } from "./client";
import { keys } from "./queries";

/**
 * Sync now and Cancel. Either way the status is refetched straight after, which also
 * switches its polling to the right pace. A 409 means the state had moved on (a run
 * already started, or already over): the refetch shows what is true, and a toast says so.
 */
export function useSyncActions() {
  const client = useQueryClient();
  const toast = useToast();
  const refetchStatus = () => client.invalidateQueries({ queryKey: keys.status });

  const start = useMutation({
    mutationFn: () => unwrap(api.POST("/api/sync")),
    onSuccess: () => toast.success("Sync started."),
    onError: (error) =>
      toast.error(
        error instanceof ApiError && error.status === 409 ? "A sync is already running" : "Could not start a sync",
        error,
      ),
    onSettled: refetchStatus,
  });

  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/sync/cancel")),
    onSuccess: () => toast.success("Cancelling the run."),
    onError: (error) =>
      toast.error(
        error instanceof ApiError && error.status === 409 ? "No run to cancel" : "Could not cancel the run",
        error,
      ),
    onSettled: refetchStatus,
  });

  return { start, cancel };
}
