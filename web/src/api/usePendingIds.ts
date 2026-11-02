import { type MutationKey, useMutationState } from "@tanstack/react-query";

/**
 * The ids whose change under `mutationKey` is still saving. A control is off while its
 * own change is in flight: a second change would take the first one's optimistic value
 * as the one to roll back to. The mutation's variables must carry the `id`.
 */
export function usePendingIds(mutationKey: MutationKey): ReadonlySet<number> {
  const ids = useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (mutation) => (mutation.state.variables as { id: number } | undefined)?.id,
  });
  return new Set(ids.filter((id): id is number => id !== undefined));
}
