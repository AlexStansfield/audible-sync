import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/Toasts";
import { plural } from "../lib/format";
import { api, unwrap } from "./client";

/** The result of Retry unavailable. */
export function requeuedText(requeued: number): string {
  if (requeued === 0) {
    return "Nothing to retry: the books left are titles Audible withdrew, which come back on their own.";
  }
  return `${plural(requeued, "book")} queued again for the next run.`;
}

/**
 * Retry unavailable (the Dashboard's Needs attention and the Library's header): every
 * licence-refused book back in the queue, one account's when given. Titles Audible
 * withdrew stay parked; the sync brings them back.
 */
export function useRetryUnavailable() {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (accountId: number | undefined) =>
      unwrap(
        api.POST("/api/books/retry-unavailable", { body: accountId === undefined ? {} : { account_id: accountId } }),
      ),
    onSuccess: ({ requeued }) => {
      toast.success(requeuedText(requeued));
      for (const queryKey of [["stats"], ["books"], ["queue"]]) void client.invalidateQueries({ queryKey });
    },
    onError: (error) => toast.error("Could not retry the unavailable books", error),
  });
}
