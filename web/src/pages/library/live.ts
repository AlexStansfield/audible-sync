/**
 * The book a run is downloading, as the Library shows it: which one, and how far its
 * audio has come.
 */
import type { components } from "../../api/schema";

type RunSnapshot = components["schemas"]["RunSnapshot"];

/** The downloader's names for a book's accessories (`src/downloader.py`), which follow the audio */
const ACCESSORIES = new Set(["PDF", "Cover"]);

/**
 * The book in hand and the audio's progress (0-1). `null` progress - the strip's sliding
 * stripe - when the size is unknown and for everything after the audio (the PDF and the
 * cover, then the decryption), so the strip does not run from 0 again for each of them.
 */
export function liveBook(run: RunSnapshot | null | undefined): { id: number; fraction: number | null } | null {
  const id = run?.book?.id;
  if (id == null) return null;
  const transfer = run?.transfer;
  const total = transfer?.total;
  const audio = transfer && total && !ACCESSORIES.has(transfer.desc ?? "");
  return { id, fraction: audio ? Math.min(1, transfer.bytes / total) : null };
}
