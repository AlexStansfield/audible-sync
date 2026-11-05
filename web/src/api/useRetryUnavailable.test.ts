import { describe, expect, it } from "vitest";
import { requeuedText } from "./useRetryUnavailable";

describe("requeuedText", () => {
  it("counts the books queued, or says why there were none", () => {
    expect(requeuedText(1)).toBe("1 book is waiting to download again.");
    expect(requeuedText(3)).toBe("3 books are waiting to download again.");
    expect(requeuedText(0)).toMatch(/^Nothing to retry/);
  });
});
