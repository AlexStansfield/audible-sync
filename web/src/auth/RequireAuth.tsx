import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback } from "react";
import { Navigate, useLocation } from "react-router";
import { ApiError } from "../api/client";
import { useMe } from "../api/queries";
import { ErrorCard } from "../components/ErrorCard";
import { loginPath } from "../lib/next";
import styles from "./auth.module.css";

/**
 * The signed-in pages: while `GET /api/auth/me` answers, show them; on a 401, the
 * sign-in page with `next` set so it comes back here afterwards.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();

  if (me.isPending) {
    return <div className={styles.centred} role="status" aria-busy="true" aria-label="Loading" />;
  }
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate to={loginPath(location.pathname + location.search)} replace />;
    }
    return (
      <div className={styles.centred}>
        <div className={styles.narrow}>
          <ErrorCard title="Could not check your sign-in" error={me.error} onRetry={() => void me.refetch()} />
        </div>
      </div>
    );
  }
  return children;
}

/** Forgets what the previous session loaded, keeping only the version. */
export function useForgetSession(): () => void {
  const client = useQueryClient();
  return useCallback(() => client.removeQueries({ predicate: (query) => query.queryKey[0] !== "health" }), [client]);
}
