/**
 * How a catalogue book reads (DESIGN.md, "A catalogue book"), kept out of the
 * components so it can be tested on its own.
 */
import type { components } from "../api/schema";
import { formatLength, plural } from "../lib/format";

export type CatalogBook = components["schemas"]["CatalogBookOut"];
export type Account = components["schemas"]["AccountOut"];
type Price = components["schemas"]["CatalogPriceOut"];

/** An amount in the price's currency, for the browser's locale: `£6.49`. */
export function formatMoney(amount: number, currency: string, locale?: string): string {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(amount);
  } catch {
    // A currency code the browser does not know
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/**
 * The price block: the member price, the list price struck through before it when it
 * is higher, and the credits. One price when the two are equal or the member price is
 * not known; nothing at all without a price.
 */
export function priceParts(
  price: Price | null,
  locale?: string,
): { main: string; was: string | null; credits: string | null } | null {
  if (!price) return null;
  const member = price.member_price;
  const list = price.list_price;
  const main = member ?? list;
  if (main == null) return null;
  return {
    main: formatMoney(main, price.currency, locale),
    was: member != null && list != null && list > member ? formatMoney(list, price.currency, locale) : null,
    credits: price.credits ? `or ${plural(price.credits, "credit")}` : null,
  };
}

/** The series line: `Old Man's War, Book 1`; empty when there is none. */
export function seriesLine(book: CatalogBook): string {
  const series = book.primary_series;
  if (!series?.title) return "";
  return series.sequence ? `${series.title}, Book ${series.sequence}` : series.title;
}

/** The year of a release date, `2007`; empty when there is none. */
export function releaseYear(date: string | null): string {
  return date?.slice(0, 4) ?? "";
}

/** The rating as `★ 4.6 (41,880)`; empty with no ratings. */
export function ratingText(book: CatalogBook): string {
  if (book.rating == null || book.rating_count === 0) return "";
  return `★ ${book.rating.toFixed(1)} (${book.rating_count.toLocaleString()})`;
}

/** The line of facts: length, "Released 2007" and the rating, whichever are known. */
export function factsLine(book: CatalogBook): string[] {
  const year = releaseYear(book.release_date);
  return [
    formatLength(book.length_seconds == null ? null : Math.round(book.length_seconds / 60)),
    year && `Released ${year}`,
    ratingText(book),
  ].filter(Boolean);
}

/** `Alex (UK)'s wishlist`, or `your wishlist` when there is only one account. */
export function wishlistName(account: Account | undefined, accounts: number): string {
  return account && accounts > 1 ? `${account.name}'s wishlist` : "your wishlist";
}

/** `Alex (UK)'s library`, or `your library` with one account. */
export function libraryName(account: Account | undefined, accounts: number): string {
  return account && accounts > 1 ? `${account.name}'s library` : "your library";
}

/**
 * Which accounts a book can be added for, and which it can come off: the signed-in
 * accounts of the searched marketplace that neither own it nor have it on their
 * wishlist, and those that have it on theirs (DESIGN.md, "The action").
 */
export function wishlistChoices(
  book: CatalogBook,
  accounts: Account[],
  countryCode: string | undefined,
): { add: Account[]; remove: Account[] } {
  const market = accounts.filter(
    (account) => !account.needs_login && (!countryCode || account.country_code === countryCode),
  );
  const owners = new Set(book.library.map((copy) => copy.account_id));
  const wished = new Set(book.on_wishlist);
  return {
    add: market.filter((account) => !owners.has(account.id) && !wished.has(account.id)),
    remove: market.filter((account) => wished.has(account.id)),
  };
}

/**
 * What tells an edition of a series apart from its siblings: the narrator, the length,
 * the year, and a word on how it differs when that can be told.
 */
export function editionDifference(book: CatalogBook): string {
  return [
    book.narrators.length ? `Narrated by ${book.narrators.join(", ")}` : "",
    formatLength(book.length_seconds == null ? null : Math.round(book.length_seconds / 60)),
    releaseYear(book.release_date),
    book.multipart ? "the same audio in parts" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The edition a position shows first: one a library here holds, else one on a
 * wishlist, else the one with the most ratings (DESIGN.md, Series).
 */
export function shownEdition<T extends { book: CatalogBook }>(editions: T[]): T | undefined {
  return (
    editions.find((entry) => entry.book.in_library) ??
    editions.find((entry) => entry.book.on_wishlist.length > 0) ??
    [...editions].sort((a, b) => b.book.rating_count - a.book.rating_count)[0]
  );
}

/** A series' books grouped by position, in reading order; books with no position last. */
export function byPosition<T extends { sequence: string | null }>(
  books: T[],
): { sequence: string | null; editions: T[] }[] {
  const groups = new Map<string, { sequence: string | null; editions: T[] }>();
  for (const entry of books) {
    const key = entry.sequence ?? "";
    const group = groups.get(key) ?? { sequence: entry.sequence, editions: [] };
    group.editions.push(entry);
    groups.set(key, group);
  }
  return [...groups.values()];
}
