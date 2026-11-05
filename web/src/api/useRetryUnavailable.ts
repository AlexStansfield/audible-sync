import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/Toasts";
import { plural } from "../lib/format";
import { api, unwrap } from "./client";
import { refreshAfterBookChange } from "./queries";

/** The result of Retry unavailable. */
export function requeuedText(requeued: number): string {
  if (requeued === 0) {
    return "Nothing to retry: the books left are titles Audible withdrew, which come back on their own.";
  }
  return `${plural(requeued, "book")} ${requeued === 1 ? "is" : "are"} waiting to download again.`;
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
      void refreshAfterBookChange(client);
    },
    onError: (error) => toast.error("Could not retry the unavailable books", error),
  });
}
