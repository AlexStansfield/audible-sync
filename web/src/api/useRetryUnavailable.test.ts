import { describe, expect, it } from "vitest";
import { requeuedText } from "./useRetryUnavailable";

describe("requeuedText", () => {
  it("counts the books queued, or says why there were none", () => {
    expect(requeuedText(3)).toBe("3 books queued again for the next run.");
    expect(requeuedText(0)).toMatch(/^Nothing to retry/);
  });
});
