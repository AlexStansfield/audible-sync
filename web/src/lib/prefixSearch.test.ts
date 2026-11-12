import { describe, expect, it } from "vitest";
import { composeSearch, describeSearch, isEmptySearch, parseSearch, searchParts, withoutPart } from "./prefixSearch";

describe("the search box's prefixes", () => {
  it("reads a value to the next prefix or the end, keywords before the first", () => {
    expect(parseSearch("colonial author:John Scalzi series:Old Man's War")).toEqual({
      keywords: "colonial",
      author: "John Scalzi",
      series: "Old Man's War",
    });
  });

  it("takes plain words as keywords", () => {
    expect(parseSearch("  old   man's war ")).toEqual({ keywords: "old man's war" });
  });

  it("counts only the four words, at the start of a word, in any case", () => {
    expect(parseSearch("Star Wars: Trials of the Jedi")).toEqual({ keywords: "Star Wars: Trials of the Jedi" });
    expect(parseSearch("coauthor:Smith")).toEqual({ keywords: "coauthor:Smith" });
    expect(parseSearch("AUTHOR:Niven Title:Ringworld")).toEqual({ keywords: "", author: "Niven", title: "Ringworld" });
  });

  it("keeps the last of a field given twice, and drops an empty one", () => {
    expect(parseSearch("author:Niven author:Pournelle narrator:")).toEqual({ keywords: "", author: "Pournelle" });
  });

  it("lists the parts in the order typed", () => {
    expect(searchParts("dragons narrator:Ray Porter author:Novik")).toEqual([
      { field: "keywords", value: "dragons" },
      { field: "narrator", value: "Ray Porter" },
      { field: "author", value: "Novik" },
    ]);
  });

  it("takes a part out, as removing its chip does", () => {
    expect(withoutPart("colonial author:John Scalzi series:Old Man's War", "author")).toBe(
      "colonial series:Old Man's War",
    );
    expect(withoutPart("colonial author:John Scalzi", "keywords")).toBe("author:John Scalzi");
    expect(composeSearch([])).toBe("");
  });

  it("says when there is nothing to search", () => {
    expect(isEmptySearch("  ")).toBe(true);
    expect(isEmptySearch("author:")).toBe(true);
    expect(isEmptySearch("author:x")).toBe(false);
  });

  it("restates a search in words", () => {
    expect(describeSearch("zombies author:John Scalzi")).toBe("author John Scalzi with the keywords “zombies”");
    expect(describeSearch("series:Broken Empire")).toBe("series “Broken Empire”");
    expect(describeSearch("zombies")).toBe("“zombies”");
  });
});
