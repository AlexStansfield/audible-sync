import { describe, expect, it } from "vitest";
import { parseSearch } from "../lib/prefixSearch";
import { account, catalogBook } from "../test/fixtures";
import {
  byPosition,
  editionDifference,
  factsLine,
  narrowsSeries,
  priceParts,
  shownEdition,
  wishlistChoices,
} from "./text";

const UK = account();
const UK2 = account({ id: 2, name: "Sam (UK)" });
const US = account({ id: 3, name: "Alex (US)", country_code: "us" });

describe("a catalogue book's wording", () => {
  it("shows the member price with the list price struck before it, and the credits", () => {
    expect(priceParts(catalogBook().price, "en-GB")).toEqual({ main: "£6.49", was: "£8.61", credits: "or 1 credit" });
    // One price when the member pays the list price, or Audible did not say
    const same = { currency: "GBP", list_price: 8.61, member_price: 8.61, credits: null };
    expect(priceParts(same, "en-GB")).toEqual({ main: "£8.61", was: null, credits: null });
    expect(priceParts({ ...same, member_price: null }, "en-GB")?.main).toBe("£8.61");
    expect(priceParts(null)).toBeNull();
  });

  it("lists the facts it has", () => {
    expect(factsLine(catalogBook())).toEqual(["9h 55m", "Released 2007", "★ 4.5 (2,325)"]);
    expect(factsLine(catalogBook({ length_seconds: null, rating: null, rating_count: 0, release_date: null }))).toEqual(
      [],
    );
  });

  it("offers Add to the marketplace's accounts that neither own it nor wish for it", () => {
    const book = catalogBook({
      library: [{ account_id: 1, book_id: 7, status: "downloaded" }],
      on_wishlist: [2],
    });
    const pending = account({ id: 4, name: "Pending (UK)", needs_login: true });

    expect(wishlistChoices(book, [UK, UK2, US, pending], "uk")).toEqual({ add: [], remove: [UK2] });
    expect(wishlistChoices(catalogBook(), [UK, UK2, US], "uk").add).toEqual([UK, UK2]);
  });

  it("offers no Add when the marketplace is not known, rather than every marketplace's", () => {
    expect(wishlistChoices(catalogBook({ on_wishlist: [3] }), [UK, UK2, US], undefined)).toEqual({
      add: [],
      remove: [US],
    });
  });

  it("shows first the edition a library holds, then one wished for, then the most rated", () => {
    const plain = { sequence: "1", book: catalogBook({ asin: "A", rating_count: 10 }) };
    const rated = { sequence: "1", book: catalogBook({ asin: "B", rating_count: 900 }) };
    const wished = { sequence: "1", book: catalogBook({ asin: "C", on_wishlist: [1] }) };
    const held = { sequence: "1", book: catalogBook({ asin: "D", in_library: true }) };

    expect(shownEdition([plain, rated])?.book.asin).toBe("B");
    expect(shownEdition([plain, rated, wished])?.book.asin).toBe("C");
    expect(shownEdition([plain, wished, held])?.book.asin).toBe("D");
  });

  it("groups a series by position in reading order", () => {
    const entries = [
      { sequence: null, book: catalogBook({ asin: "E" }) },
      { sequence: "1", book: catalogBook({ asin: "A" }) },
      { sequence: "1", book: catalogBook({ asin: "B" }) },
      { sequence: "4.5", book: catalogBook({ asin: "C" }) },
      { sequence: null, book: catalogBook({ asin: "D" }) },
    ];

    expect(byPosition(entries).map((group) => [group.sequence, group.editions.length])).toEqual([
      ["1", 2],
      ["4.5", 1],
      [null, 2],
    ]);
  });

  it("narrows a series' books by the rest of the box, as the service compares", () => {
    const zoe = catalogBook({ title: "Zoe’s Tale", narrators: ["Tavia Gilbert"] });
    const omw = catalogBook();
    const narrows = (text: string, book = zoe) => narrowsSeries(book, parseSearch(`${text} series:Old Man's War`));

    // Curly apostrophes and case folded, as `fold_text` does
    expect(narrows("title:zoe's tale")).toBe(true);
    // Keywords word by word, anywhere in the title, authors or narrators
    expect(narrows("tale scalzi")).toBe(true);
    expect(narrows("tale gilbert")).toBe(true);
    expect(narrows("colonial")).toBe(false);
    expect(narrows("narrator:Tavia")).toBe(true);
    expect(narrows("narrator:Dufris")).toBe(false);
    expect(narrows("", omw)).toBe(true);
  });

  it("says what tells an edition apart", () => {
    expect(editionDifference(catalogBook({ multipart: true }))).toBe(
      "Narrated by William Dufris · 9h 55m · 2007 · the same audio in parts",
    );
  });
});
