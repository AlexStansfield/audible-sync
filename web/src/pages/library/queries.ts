/**
 * What the Library reads and changes. Keys start with `books` (or `stats`), so the end
 * of a run refetches them (`AFTER_RUN`).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import type { components } from "../../api/schema";
import { useToast } from "../../components/Toasts";
import { type LibraryQuery, listParams } from "./libraryQuery";

type BookList = components["schemas"]["BookList"];
type BulkRequest = components["schemas"]["BulkRequest"];

export const listKey = (query: LibraryQuery) => ["books", "list", listParams(query)] as const;

/** What a change to books moves: every listing, the totals, the queue, the sidebar's count. */
const CHANGED_BY_BOOKS = [["books"], ["stats"], ["queue"]] as const;

/** One page of the library; the previous page stays on show while the next loads. */
export function useLibrary(query: LibraryQuery) {
  return useQuery({
    queryKey: listKey(query),
    queryFn: ({ signal }) => unwrap(api.GET("/api/books", { params: { query: listParams(query) }, signal })),
    placeholderData: keepPreviousData,
  });
}

/**
 * The bookmark and the table's switch: `PATCH /api/books/{id}`, shown at once on the
 * page in view and put back if the service refuses.
 */
export function useMonitorBook(query: LibraryQuery) {
  const client = useQueryClient();
  const toast = useToast();
  const key = listKey(query);
  return useMutation({
    mutationFn: ({ id, monitored }: { id: number; monitored: boolean }) =>
      unwrap(api.PATCH("/api/books/{book_id}", { params: { path: { book_id: id } }, body: { monitored } })),
    onMutate: async ({ id, monitored }) => {
      // A refetch landing after this would put the old value back
      await client.cancelQueries({ queryKey: key });
      const before = client.getQueryData<BookList>(key);
      client.setQueryData<BookList>(
        key,
        (list) =>
          list && { ...list, items: list.items.map((book) => (book.id === id ? { ...book, monitored } : book)) },
      );
      return { before };
    },
    onError: (error, { monitored }, context) => {
      if (context?.before) client.setQueryData(key, context.before);
      toast.error(monitored ? "Could not monitor the book" : "Could not stop monitoring the book", error);
    },
    onSettled: () => {
      for (const queryKey of CHANGED_BY_BOOKS) void client.invalidateQueries({ queryKey: [...queryKey] });
    },
  });
}

/** `POST /api/books/bulk`; the caller words the result. */
export function useBulkAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkRequest) => unwrap(api.POST("/api/books/bulk", { body })),
    onSettled: () => {
      for (const queryKey of CHANGED_BY_BOOKS) void client.invalidateQueries({ queryKey: [...queryKey] });
    },
  });
}
