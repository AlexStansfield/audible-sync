/**
 * Whether the service answers at all. A request that gets no response (not an error
 * status) sets off one check of `GET /api/health`; only if that fails too is the
 * service down, so one dropped poll does not flash the Unreachable page. While down it
 * is checked again with backoff, counting down the seconds, and when it answers every
 * query is refetched.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiBaseUrl, onUnreachable } from "../api/client";

/** Seconds before each retry; the last repeats. */
export const BACKOFF = [2, 4, 8, 15, 30] as const;

export function retryDelay(attempt: number): number {
  return BACKOFF[Math.min(attempt, BACKOFF.length - 1)] ?? 30;
}

/** A health check outside the API client, so it does not set off its own listeners. */
export async function checkHealth(): Promise<boolean> {
  try {
    const response = await globalThis.fetch(`${apiBaseUrl()}/api/health`, { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}

export type Reachability = {
  down: boolean;
  secondsLeft: number;
  checking: boolean;
  retryNow: () => void;
};

export function useReachability(check: () => Promise<boolean> = checkHealth): Reachability {
  const client = useQueryClient();
  const [down, setDown] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [checking, setChecking] = useState(false);
  const busy = useRef(false);
  const isDown = useRef(false);

  const probe = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setChecking(true);
    const ok = await check();
    busy.current = false;
    setChecking(false);
    if (ok) {
      if (isDown.current) {
        isDown.current = false;
        setDown(false);
        setAttempt(0);
        void client.invalidateQueries();
      }
    } else if (isDown.current) {
      setAttempt((current) => current + 1);
    } else {
      isDown.current = true;
      setAttempt(0);
      setDown(true);
    }
  }, [check, client]);

  useEffect(
    () =>
      onUnreachable(() => {
        if (!isDown.current) void probe();
      }),
    [probe],
  );

  // While down: count down to the next check
  useEffect(() => {
    if (!down || checking) return;
    let left = retryDelay(attempt);
    setSecondsLeft(left);
    const timer = setInterval(() => {
      left -= 1;
      setSecondsLeft(Math.max(0, left));
      if (left <= 0) {
        clearInterval(timer);
        void probe();
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [down, attempt, checking, probe]);

  return { down, secondsLeft, checking, retryNow: () => void probe() };
}
