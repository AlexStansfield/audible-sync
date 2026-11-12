import { describe, expect, it } from "vitest";
import { pageList, rangeText } from "./paging";

describe("the pager", () => {
  it("lists every page when there are few", () => {
    expect(pageList(1, 1)).toEqual([1]);
    expect(pageList(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("keeps the ends and the pages around the current one, with gaps between", () => {
    expect(pageList(1, 15)).toEqual([1, 2, 3, 4, null, 15]);
    expect(pageList(7, 15)).toEqual([1, null, 6, 7, 8, null, 15]);
    expect(pageList(15, 15)).toEqual([1, null, 12, 13, 14, 15]);
    expect(pageList(3, 15)).toEqual([1, 2, 3, 4, null, 15]);
  });

  it("words the range shown", () => {
    expect(rangeText(1, 24, 354, "0 books")).toBe("1–24 of 354");
    expect(rangeText(15, 24, 354, "0 books")).toBe("337–354 of 354");
    expect(rangeText(1, 24, 0, "0 books")).toBe("0 books");
    expect(rangeText(1, 20, 0, "0 results")).toBe("0 results");
  });
});
