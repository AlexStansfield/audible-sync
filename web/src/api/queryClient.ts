import { QueryClient } from "@tanstack/react-query";
import { ApiError, NetworkError } from "./client";

/**
 * Retry a failed query once, unless retrying cannot help: an error status the API
 * meant (4xx), or no answer at all, which the Unreachable page takes over.
 */
export function shouldRetry(failures: number, error: unknown): boolean {
  if (error instanceof NetworkError) return false;
  if (error instanceof ApiError && error.status < 500) return false;
  return failures < 1;
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry },
      mutations: { retry: false },
    },
  });
}
