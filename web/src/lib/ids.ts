/**
 * An id from the address - a path segment or a query value: a positive whole number
 * that fits a safe integer, else null, so nothing is asked of the API for `abc`, `0`
 * or `1e3`.
 */
export function positiveId(value: string | null | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id >= 1 ? id : null;
}
