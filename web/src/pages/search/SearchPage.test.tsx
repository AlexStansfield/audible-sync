import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fail, fakeApi, json, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { account, catalogBook } from "../../test/fixtures";
import { renderApp } from "../../test/render";

const UK = account();
const UK2 = account({ id: 2, name: "Sam (UK)" });
const US = account({ id: 3, name: "Alex (US)", country_code: "us" });

const MARKETS = [
  { country_code: "uk", domain: "co.uk", name: "United Kingdom" },
  { country_code: "us", domain: "com", name: "United States" },
];

const CATEGORIES = [
  {
    id: "19378442031",
    name: "Science Fiction & Fantasy",
    children: [{ id: "19378476031", name: "Military", children: [] }],
  },
];

const OMW = catalogBook();
const GHOST = catalogBook({
  asin: "B06XNYHSP9",
  title: "The Ghost Brigades",
  primary_series: { title: "Old Man's War", sequence: "2", series_asin: "B00HNXRO2O" },
  in_library: true,
  library_book_id: 42,
  library: [{ account_id: 1, book_id: 42, status: "downloaded" }],
  plus: true,
});

function results(items = [OMW, GHOST], total = items.length, reachable = Math.min(total, 1000)) {
  return (request: Request) => {
    const query = new URL(request.url).searchParams;
    return json(200, { items, total, reachable, page: Number(query.get("page") ?? 1), page_size: 20, account_id: 1 });
  };
}

function catalogue(accounts = [UK], extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/status": ok(status({ accounts })),
      "GET /api/marketplaces": ok(MARKETS),
      "GET /api/catalog/categories": ok(CATEGORIES),
      "GET /api/catalog/search": results(),
      "GET /api/wishlist": ok({ items: [], total: 0, page: 1, page_size: 1, accounts: [] }),
      ...extra,
    }),
  );
}

function searches(api: ReturnType<typeof fakeApi>): URLSearchParams[] {
  return api.calls
    .filter((call) => call.method === "GET" && call.path === "/api/catalog/search")
    .map((call) => new URL(call.url).searchParams);
}

describe("Search", () => {
  it("opens on what it is for, and an example fills the box and searches", async () => {
    const api = catalogue();
    renderApp("/search");

    expect(await screen.findByRole("heading", { name: "Search the Audible catalogue" })).toBeInTheDocument();
    expect(await screen.findByText("The Audible catalogue on audible.co.uk, with your prices")).toBeInTheDocument();
    expect(searches(api)).toEqual([]);

    await userEvent.click(screen.getByRole("button", { name: "author:Adrian Tchaikovsky" }));

    await waitFor(() => expect(searches(api).at(-1)?.get("author")).toBe("Adrian Tchaikovsky"));
    expect(screen.getByRole("searchbox", { name: "Search the catalogue" })).toHaveValue("author:Adrian Tchaikovsky");
  });

  it("asks Audible for what the box was read as, and shows the chips", async () => {
    const api = catalogue();
    renderApp("/search?q=colonial%20author%3AJohn%20Scalzi&sort=newest");

    await screen.findByRole("article", { name: "Old Man's War" });
    const query = searches(api)[0];
    expect(query?.get("keywords")).toBe("colonial");
    expect(query?.get("author")).toBe("John Scalzi");
    expect(query?.get("sort")).toBe("newest");
    expect(query?.get("page_size")).toBe("20");

    const chips = screen.getByRole("list", { name: "Searching for" });
    expect(within(chips).getByText("John Scalzi")).toBeInTheDocument();
    await userEvent.click(within(chips).getByRole("button", { name: "Remove author John Scalzi" }));
    await waitFor(() => expect(searches(api).at(-1)?.get("author")).toBeNull());
    expect(screen.getByRole("searchbox", { name: "Search the catalogue" })).toHaveValue("colonial");
  });

  it("shows a book's details, markers and price", async () => {
    catalogue();
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    expect(within(omw).getByText("Old Man's War, Book 1")).toBeInTheDocument();
    expect(within(omw).getByText("9h 55m · Released 2007 · ★ 4.5 (2,325)")).toBeInTheDocument();
    expect(within(omw).getByText(/6\.49/)).toBeInTheDocument();
    expect(within(omw).getByText("or 1 credit")).toBeInTheDocument();
    expect(screen.getByText("2 results on audible.co.uk")).toBeInTheDocument();

    const ghost = screen.getByRole("article", { name: "The Ghost Brigades" });
    expect(within(ghost).getByRole("link", { name: "In library →" })).toHaveAttribute("href", "/library/42");
    expect(within(ghost).getByText("Plus")).toBeInTheDocument();
    // The account owns it: Audible's wishlist is no place for it
    expect(within(ghost).queryByRole("button", { name: /wishlist/ })).not.toBeInTheDocument();
  });

  it("adds a book to the wishlist, busy until the markers come back", async () => {
    let added = false;
    const api = catalogue([UK], {
      "GET /api/catalog/search": (request) => results([added ? catalogBook({ on_wishlist: [1] }) : OMW])(request),
      "POST /api/wishlist": () => {
        added = true;
        return json(201, { ...OMW, account_id: 1, added_at: "2026-11-12T09:00:00Z", on_wishlist: [1] });
      },
    });
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Add to wishlist" }));

    expect(await screen.findByText("Added to your wishlist")).toBeInTheDocument();
    expect(api.sent("POST /api/wishlist")).toEqual([{ account_id: 1, asin: "B06XNYQJTX" }]);
    expect(await within(omw).findByRole("button", { name: "Remove from wishlist" })).toBeInTheDocument();
    expect(within(omw).getByText("On wishlist")).toBeInTheDocument();
  });

  it("asks which account's wishlist when the marketplace has several, never another marketplace's", async () => {
    const api = catalogue([UK, UK2, US], {
      "POST /api/wishlist": () => json(201, { ...OMW, account_id: 2, added_at: null }),
    });
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Add to wishlist" }));
    const menu = within(omw).getByText("Add to the wishlist of").parentElement as HTMLElement;
    expect(
      within(menu)
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["Alex (UK)searched", "Sam (UK)"]);

    await userEvent.click(within(menu).getByRole("button", { name: "Sam (UK)" }));

    expect(await screen.findByText("Added to Sam (UK)'s wishlist")).toBeInTheDocument();
    expect(api.sent("POST /api/wishlist")).toEqual([{ account_id: 2, asin: "B06XNYQJTX" }]);
  });

  it("takes a book off the wishlist, and says so when that fails", async () => {
    const api = catalogue([UK], {
      "GET /api/catalog/search": results([catalogBook({ on_wishlist: [1] })]),
      "DELETE /api/wishlist/1/B06XNYQJTX": fail(502, "Audible did not answer: timed out"),
    });
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Remove from wishlist" }));

    expect(await screen.findByText("Could not remove Old Man's War from the wishlist")).toBeInTheDocument();
    expect(screen.getByText("Audible did not answer: timed out")).toBeInTheDocument();
    expect(within(omw).getByRole("button", { name: "Remove from wishlist" })).toBeEnabled();
    expect(api.calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  });

  it("reads the markers again when an add fails, as Audible may have made the change", async () => {
    let wished: number[] = [];
    const api = catalogue([UK], {
      "GET /api/catalog/search": (request: Request) => results([catalogBook({ on_wishlist: wished })])(request),
      "POST /api/wishlist": () => {
        wished = [1];
        return fail(502, "Audible did not answer: timed out")();
      },
    });
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Add to wishlist" }));

    expect(await within(omw).findByRole("button", { name: "Remove from wishlist" })).toBeEnabled();
    expect(api.calls.filter((call) => call.method === "POST")).toHaveLength(1);
  });

  it("stops the pager where Audible stops paging, and says so", async () => {
    catalogue([UK], { "GET /api/catalog/search": results([OMW], 4210, 1000) });
    renderApp("/search?q=science&page=50");

    expect(await screen.findByText(/Audible shows only the first 1,000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Page 50" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByText(/Showing the first 1,000 of 4,210 results/)).toBeInTheDocument();
  });

  it("never asks for a page past Audible's reach", async () => {
    const api = catalogue([UK], { "GET /api/catalog/search": results([OMW], 4210, 1000) });
    renderApp("/search?q=science&page=900");

    await screen.findByRole("article", { name: "Old Man's War" });
    expect(searches(api).map((query) => query.get("page"))).toEqual(["50"]);
  });

  it("offers the category's children once one is chosen", async () => {
    const api = catalogue();
    renderApp("/search?q=war");

    await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category" }), "19378442031");
    await userEvent.selectOptions(
      await screen.findByRole("combobox", { name: "Within Science Fiction & Fantasy" }),
      "19378476031",
    );

    await waitFor(() => expect(searches(api).at(-1)?.get("category_id")).toBe("19378476031"));
  });

  it("says when nothing matches, in words", async () => {
    catalogue([UK], { "GET /api/catalog/search": results([], 0) });
    renderApp("/search?q=zombies%20author%3AJohn%20Scalzi");

    expect(await screen.findByRole("heading", { name: "No books match this search" })).toBeInTheDocument();
    expect(
      screen.getByText("Nothing on audible.co.uk matches author John Scalzi with the keywords “zombies”."),
    ).toBeInTheDocument();
  });

  it("points to Accounts when no account can search", async () => {
    const api = catalogue([account({ needs_login: true })]);
    renderApp("/search?q=scalzi");

    expect(await screen.findByRole("heading", { name: "No account to search with" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Accounts" })).toHaveAttribute("href", "/accounts");
    expect(searches(api)).toEqual([]);
  });

  it("shows Audible not answering with a retry", async () => {
    catalogue([UK], { "GET /api/catalog/search": fail(502, "Audible did not answer: timed out") });
    renderApp("/search?q=scalzi");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not search the catalogue");
    expect(alert).toHaveTextContent("Audible did not answer: timed out");
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("opens a book's panel with its description, and closes it", async () => {
    catalogue([UK], {
      "GET /api/catalog/books/B06XNYQJTX": ok({
        ...OMW,
        description: "Old soldiers.",
        description_html: "<p>Old <strong>soldiers</strong>.</p>",
      }),
    });
    const { router } = renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Old Man's War" }));

    const panel = await screen.findByRole("dialog", { name: "Old Man's War" });
    expect(await within(panel).findByText("soldiers")).toBeInTheDocument();
    expect(within(panel).getByText("Unabridged")).toBeInTheDocument();
    expect(router.state.location.search).toContain("book=B06XNYQJTX");

    await userEvent.click(within(panel).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(router.state.location.search).not.toContain("book="));
  });

  it("leaves the category behind when another marketplace is searched, and keeps it within one", async () => {
    const api = catalogue([UK, UK2, US]);
    const { router } = renderApp("/search?q=war&category=19378442031");

    await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Search in" }), "2");
    await waitFor(() => expect(searches(api).at(-1)?.get("account_id")).toBe("2"));
    expect(searches(api).at(-1)?.get("category_id")).toBe("19378442031");

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Search in" }), "3");
    await waitFor(() => expect(searches(api).at(-1)?.get("account_id")).toBe("3"));
    expect(searches(api).at(-1)?.get("category_id")).toBeNull();
    expect(router.state.location.search).not.toContain("category=");
  });

  it("asks for a book's details only once the account to ask through is known", async () => {
    let answer: () => void = () => undefined;
    const api = catalogue([UK], {
      "GET /api/status": () =>
        new Promise<Response>((resolve) => {
          answer = () => resolve(json(200, status({ accounts: [UK] })));
        }),
      "GET /api/catalog/books/B06XNYQJTX": ok({ ...OMW, description: "", description_html: "" }),
    });
    renderApp("/search?q=scalzi&book=B06XNYQJTX");

    await screen.findByRole("dialog");
    expect(api.calls.filter((call) => call.path === "/api/catalog/books/B06XNYQJTX")).toEqual([]);
    answer();
    await waitFor(() =>
      expect(
        api.calls
          .filter((call) => call.path === "/api/catalog/books/B06XNYQJTX")
          .map((call) => new URL(call.url).searchParams.get("account_id")),
      ).toEqual(["1"]),
    );
  });

  it("says when a book's description could not be loaded, with a retry", async () => {
    catalogue([UK], { "GET /api/catalog/books/B06XNYQJTX": fail(502, "Audible did not answer") });
    renderApp("/search?q=scalzi");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Old Man's War" }));

    const panel = await screen.findByRole("dialog", { name: "Old Man's War" });
    expect(await within(panel).findByText(/Could not load the description/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("Search by series", () => {
  const SERIES = {
    asin: "B00HNXRO2O",
    title: "Old Man’s War",
    books: [
      { sequence: "1", book: catalogBook({ asin: "B0799FKB9N", rating_count: 10, multipart: true }) },
      { sequence: "1", book: OMW },
      { sequence: "2", book: GHOST },
    ],
  };

  it("goes straight to the one series that matches, its books by position", async () => {
    const api = catalogue([UK], {
      "GET /api/catalog/series": ok([{ asin: "B00HNXRO2O", title: "Old Man’s War", books_found: 6, exact: true }]),
      "GET /api/catalog/series/B00HNXRO2O": ok(SERIES),
    });
    renderApp("/search?q=series%3AOld%20Man%27s%20War");

    expect(await screen.findByRole("heading", { name: "Old Man’s War" })).toBeInTheDocument();
    expect(screen.getByText("3 books in 2 positions, in reading order · 1 in a library here")).toBeInTheDocument();
    // The edition with the most ratings shows; the other is one click away
    expect(screen.getByRole("article", { name: "Old Man's War" })).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /Hide the other edition/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/the same audio in parts/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Sort" })).toBeDisabled();
    expect(searches(api)).toEqual([]);
  });

  it("offers the series to choose from when several match", async () => {
    catalogue([UK], {
      "GET /api/catalog/series": ok([
        { asin: "B00HNXRO2O", title: "Old Man’s War", books_found: 6, exact: true },
        { asin: "B07NJ9HXZH", title: "La Vieja Guardia [Old Man's War]", books_found: 1, exact: false },
      ]),
      "GET /api/catalog/series/B00HNXRO2O": ok(SERIES),
    });
    renderApp("/search?q=series%3AOld%20Man%27s%20War");

    expect(await screen.findByRole("heading", { name: /2 series match/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Old Man’s War\s*Exact match\s*6 books found/ }));

    expect(await screen.findByRole("heading", { name: "Old Man’s War" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "← 1 other series matched" }));
    expect(await screen.findByRole("heading", { name: /2 series match/ })).toBeInTheDocument();
  });

  it("says when no series has the name", async () => {
    catalogue([UK], { "GET /api/catalog/series": ok([]) });
    renderApp("/search?q=series%3ANothing%20Like%20It");

    expect(
      await screen.findByText("No series called “Nothing Like It” was found on audible.co.uk."),
    ).toBeInTheDocument();
  });
});
