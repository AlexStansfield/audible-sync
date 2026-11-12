/** Paging shared by the lists: which page buttons to show. */

/**
 * The page buttons: the first, the last, and the current one with a neighbour each
 * side, `null` marking a gap - `1 2 3 … 15`, `1 … 6 7 8 … 15`.
 */
export function pageList(current: number, pages: number): (number | null)[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const shown = new Set([1, pages, current - 1, current, current + 1]);
  if (current <= 3) for (const page of [2, 3, 4]) shown.add(page);
  if (current >= pages - 2) for (const page of [pages - 3, pages - 2, pages - 1]) shown.add(page);
  const sorted = [...shown].filter((page) => page >= 1 && page <= pages).sort((a, b) => a - b);
  const list: (number | null)[] = [];
  for (const page of sorted) {
    const last = list[list.length - 1];
    if (typeof last === "number" && page - last > 1) list.push(null);
    list.push(page);
  }
  return list;
}
