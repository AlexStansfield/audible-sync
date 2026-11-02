/**
 * What the Accounts screen reads and changes. The list is `["accounts"]`, one of
 * `AFTER_RUN`'s keys, so the end of a run refetches its last-synced times.
 */
import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { ApiError, api, unwrap } from "../../api/client";
import { keys, useStatus } from "../../api/queries";
import { useToast } from "../../components/Toasts";
import { type Account, LOGIN_POLL, type Login, type Purpose } from "./login";

export const ACCOUNTS_KEY = ["accounts"] as const;
export const ENABLED_KEY = ["account-enabled"] as const;
const loginKey = (loginId: string) => ["login", loginId] as const;

/**
 * What a change to accounts moves: the list, the status (the sidebar's dot and the
 * Dashboard's accounts), the totals, the queue - and, with `books`, the library and the
 * run history, which lose an account's rows when it goes (and change when a sign-in
 * again queues its refused books).
 */
export function refreshAfterAccountChange(client: QueryClient, { books = false } = {}): Promise<unknown> {
  const changed = [ACCOUNTS_KEY, keys.status, ["stats"], ["queue"], ...(books ? [["books"], ["runs"]] : [])];
  return Promise.all(changed.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })));
}

export function useAccounts() {
  return useQuery({ queryKey: ACCOUNTS_KEY, queryFn: () => unwrap(api.GET("/api/accounts")) });
}

/**
 * The polled status carries the accounts too. When its list moves - an account a code
 * login confirmed after its dialog closed, a sign-in that cleared Needs sign-in - the
 * list here is read again, within one status poll.
 */
export function useFollowStatus(): void {
  const client = useQueryClient();
  const status = useStatus();
  const shape = status.data?.accounts.map((account) => `${account.id}:${account.needs_login}`).join(",");
  const seen = useRef(shape);
  useEffect(() => {
    if (shape !== undefined && seen.current !== undefined && shape !== seen.current) {
      void client.invalidateQueries({ queryKey: ACCOUNTS_KEY });
    }
    if (shape !== undefined) seen.current = shape;
  }, [shape, client]);
}

/** The marketplaces never change while the service runs. */
export function useMarketplaces() {
  return useQuery({
    queryKey: ["marketplaces"],
    queryFn: () => unwrap(api.GET("/api/marketplaces")),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** The Sync switch: shown at once, put back with a toast if the service refuses. */
export function useSetEnabled() {
  const client = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationKey: ENABLED_KEY,
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      unwrap(api.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: id } }, body: { enabled } })),
    onMutate: async ({ id, enabled }) => {
      await client.cancelQueries({ queryKey: ACCOUNTS_KEY });
      const before = client.getQueryData<Account[]>(ACCOUNTS_KEY);
      client.setQueryData<Account[]>(ACCOUNTS_KEY, (accounts) =>
        accounts?.map((account) => (account.id === id ? { ...account, enabled } : account)),
      );
      return { before };
    },
    onError: (error, { enabled }, context) => {
      if (context?.before) client.setQueryData(ACCOUNTS_KEY, context.before);
      toast.error(enabled ? "Could not turn syncing on" : "Could not pause the account", error);
    },
    onSettled: () => refreshAfterAccountChange(client),
  });
}

export function useRename() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      unwrap(api.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: id } }, body: { name } })),
    onSuccess: () => {
      void refreshAfterAccountChange(client);
    },
  });
}

export function useRemove() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, deregister }: { id: number; deregister: boolean }) =>
      unwrap(api.DELETE("/api/accounts/{account_id}", { params: { path: { account_id: id }, query: { deregister } } })),
    onSuccess: () => {
      void refreshAfterAccountChange(client, { books: true });
    },
  });
}

/** What the dialog's setup step chose. */
export type LoginChoice = {
  countryCode: string;
  name: string;
  monitorExisting: boolean;
  deregisterPrevious: boolean;
};

/** `POST /api/accounts/login`, for a new account or for `purpose`'s account. */
export function useStartLogin(purpose: Purpose) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ method, choice }: { method: "code" | "browser"; choice: LoginChoice }) =>
      unwrap(
        api.POST("/api/accounts/login", {
          body: {
            country_code: choice.countryCode,
            method,
            ...(purpose.kind === "relogin"
              ? { account_id: purpose.account.id, monitor_existing: true }
              : { name: choice.name.trim() || null, monitor_existing: choice.monitorExisting }),
            deregister_previous: choice.deregisterPrevious,
          },
        }),
      ),
    // The answer is the login's state now: what the code step starts from, never an older poll
    onSuccess: (login) => client.setQueryData(loginKey(login.login_id), login),
  });
}

/**
 * A code login as it stands, every 3 s while it is waiting for the user. A failed poll
 * (a dropped request, a 5xx from a proxy) only waits for the next one: the login is
 * still going on the service. A 404 means the service has forgotten it (a while after it
 * ended, or a restart): polling stops and the dialog reads that as expired.
 */
export function useLoginStatus(login: Login | null) {
  const loginId = login?.login_id ?? "";
  return useQuery({
    queryKey: loginKey(loginId),
    queryFn: () => unwrap(api.GET("/api/accounts/login/{login_id}", { params: { path: { login_id: loginId } } })),
    enabled: login !== null && login.method === "code",
    initialData: login ?? undefined,
    // The answer the start gave is as fresh as a poll; the first poll is one interval on
    initialDataUpdatedAt: () => Date.now(),
    staleTime: LOGIN_POLL,
    // Until the service says it is confirmed, failed or expired, or has forgotten it
    refetchInterval: (query) =>
      query.state.data?.status === "pending" && !isForgotten(query.state.error) ? LOGIN_POLL : false,
    retry: false,
  });
}

/** A 404 from the login's poll: the service no longer knows it. */
export function isForgotten(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** Step two of a browser sign-in: the pasted address. */
export function useCompleteLogin() {
  return useMutation({
    mutationFn: ({ loginId, responseUrl }: { loginId: string; responseUrl: string }) =>
      unwrap(
        api.POST("/api/accounts/login/{login_id}", {
          params: { path: { login_id: loginId } },
          body: { response_url: responseUrl },
        }),
      ),
  });
}
