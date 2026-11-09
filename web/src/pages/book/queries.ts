/**
 * What Book detail reads and changes. The book is `["books", id]` - the Dashboard's
 * `useBook` - so the end of a run refetches it with every other `books` key.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { bookKey, refreshAfterBookChange, settingsQuery } from "../../api/queries";
import type { components } from "../../api/schema";
import { useToast } from "../../components/Toasts";

type Book = components["schemas"]["BookDetail"];

export type BookActionName = "redownload" | "retry" | "delete-files" | "refresh";

/** `max_attempts`, for "1 of 3". */
export function useMaxAttempts() {
  return useQuery({
    ...settingsQuery,
    select: (settings) => settings.max_attempts,
  });
}

/** The Monitored switch: shown at once, put back if the service refuses. */
export function useMonitor(bookId: number) {
  const client = useQueryClient();
  const toast = useToast();
  const key = bookKey(bookId);
  return useMutation({
    mutationFn: (monitored: boolean) =>
      unwrap(api.PATCH("/api/books/{book_id}", { params: { path: { book_id: bookId } }, body: { monitored } })),
    onMutate: async (monitored) => {
      await client.cancelQueries({ queryKey: key });
      const before = client.getQueryData<Book>(key);
      client.setQueryData<Book>(key, (book) => book && { ...book, monitored });
      return { before };
    },
    onError: (error, monitored, context) => {
      if (context?.before) client.setQueryData(key, context.before);
      toast.error(monitored ? "Could not monitor the book" : "Could not stop monitoring the book", error);
    },
    onSettled: () => {
      void refreshAfterBookChange(client);
    },
  });
}

/** Redownload, Retry, Delete files and Refresh; the page words the outcome. */
export function useBookAction(bookId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (action: BookActionName) => {
      const path = { params: { path: { book_id: bookId } } };
      switch (action) {
        case "redownload":
          return unwrap(api.POST("/api/books/{book_id}/redownload", path));
        case "retry":
          return unwrap(api.POST("/api/books/{book_id}/retry", path));
        case "delete-files":
          return unwrap(api.POST("/api/books/{book_id}/delete-files", path));
        case "refresh":
          return unwrap(api.POST("/api/books/{book_id}/refresh", path));
      }
    },
    onSettled: () => {
      void refreshAfterBookChange(client);
    },
  });
}
