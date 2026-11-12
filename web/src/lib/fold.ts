/**
 * Text for comparing a name or title: case, curly quotes and runs of spaces ignored,
 * since Audible writes "Old Man’s War" where a person types "Old Man's War". A port of
 * the service's `model.fold_text`.
 */
export function foldText(text: string | null | undefined): string {
  return (text ?? "").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, " ").trim().toLowerCase();
}
