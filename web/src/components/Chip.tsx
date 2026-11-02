import type { CSSProperties, ReactNode } from "react";
import { BOOK_STATUS, type BookStatus, RUN_OUTCOME, type RunOutcome } from "../lib/format";

export function Chip({ colour, children }: { colour: string; children: ReactNode }) {
  return (
    <span className="chip" style={{ "--chip": colour } as CSSProperties}>
      {children}
    </span>
  );
}

/** A book's status, in the same word and colour everywhere. */
export function StatusChip({ status }: { status: string | null | undefined }) {
  const known = status ? BOOK_STATUS[status as BookStatus] : undefined;
  return <Chip colour={known?.colour ?? "var(--muted)"}>{known?.label ?? status ?? "Unknown"}</Chip>;
}

/** A run's outcome. */
export function OutcomeChip({ outcome }: { outcome: string | null | undefined }) {
  const known = outcome ? RUN_OUTCOME[outcome as RunOutcome] : undefined;
  return <Chip colour={known?.colour ?? "var(--muted)"}>{known?.label ?? outcome ?? "Unknown"}</Chip>;
}
