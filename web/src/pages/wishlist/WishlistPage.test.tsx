import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fail, fakeApi, json, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { account, catalogBook, type WishlistItem, wishlistItem } from "../../test/fixtures";
import { renderApp } from "../../test/render";

const UK = account();
const US = account({ id: 2, name: "Alex (US)", country_code: "us" });

const GHOST = wishlistItem({
  asin: "B06XNYHSP9",
  title: "The Ghost Brigades",
  account_id: 2,
  in_library: true,
  library_book_id: 42,
  library: [{ account_id: 2, book_id: 42, status: "downloaded" }],
});
const OMW = wishlistItem({ on_wishlist: [1] });

const READ = (id: number, overrides = {}) => ({
  account_id: id,
  read_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
  error: null,
  error_at: null,
  needs_login: false,
  ...overrides,
});

/** The list for the page asked for, and the sidebar's one-row count. */
function listing(items: WishlistItem[], states = [READ(1), READ(2)], all = items.length) {
  return (request: Request) => {
    const query = new URL(request.url).searchParams;
    const accountId = query.get("account_id");
    const shown = accountId ? items.filter((item) => item.account_id === Number(accountId)) : items;
    if (query.get("page_size") === "1")
      return json(200, { items: [], total: all, page: 1, page_size: 1, accounts: states });
    return json(200, { items: shown, total: shown.length, page: 1, page_size: 50, accounts: states });
  };
}

function wishlist(items: WishlistItem[] = [OMW, GHOST], extra: Record<string, Reply> = {}, accounts = [UK, US]) {
  return fakeApi(
    signedIn({
      "GET /api/status": ok(status({ accounts })),
      "GET /api/wishlist": listing(items),
      ...extra,
    }),
  );
}

describe("the Wishlist", () => {
  it("lists every account's books with the account and the date, a held book first to the eye", async () => {
    wishlist();
    renderApp("/wishlist");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    expect(within(omw).getByText("Alex (UK)")).toBeInTheDocument();
    expect(within(omw).getByText(/^Added /)).toBeInTheDocument();
    // Its own wishlist goes without saying here
    expect(within(omw).queryByText(/On .*wishlist/)).not.toBeInTheDocument();
    const ghost = screen.getByRole("article", { name: "The Ghost Brigades" });
    expect(within(ghost).getByRole("link", { name: "In Alex (US)'s library →" })).toHaveAttribute(
      "href",
      "/library/42",
    );
    expect(screen.getByText("2 books on the wishlists of 2 accounts")).toBeInTheDocument();
    expect(screen.getByText("1 already in a library")).toBeInTheDocument();
    expect(screen.getByText("Updated 2h ago")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Wishlist, 2 books/ })).toBeInTheDocument();
  });

  it("removes a book once confirmed", async () => {
    const api = wishlist([OMW, GHOST], { "DELETE /api/wishlist/1/B06XNYQJTX": ok({ status: "removed" }) });
    renderApp("/wishlist");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Remove" }));
    const dialog = screen.getByRole("dialog", { name: "Remove Old Man's War from Alex (UK)'s wishlist?" });
    expect(dialog).toHaveTextContent("It comes off the wishlist on Audible too.");
    expect(api.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    await userEvent.click(within(dialog).getByRole("button", { name: "Remove from wishlist" }));

    expect(await screen.findByText("Removed Old Man's War from Alex (UK)'s wishlist")).toBeInTheDocument();
    expect(api.calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  });

  it("keeps the book when Keep it is chosen, and says so when a remove fails", async () => {
    const api = wishlist([OMW], { "DELETE /api/wishlist/1/B06XNYQJTX": fail(502, "Audible did not answer") }, [UK]);
    renderApp("/wishlist");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));
    expect(api.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);

    await userEvent.click(within(omw).getByRole("button", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove from wishlist" }));

    expect(await screen.findByText("Could not remove Old Man's War")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Old Man's War" })).toBeInTheDocument();
  });

  it("reads the wishlists again on Refresh from Audible", async () => {
    let release: () => void = () => undefined;
    const api = wishlist([OMW, GHOST], {
      "POST /api/wishlist/refresh": () =>
        new Promise<Response>((resolve) => {
          release = () => resolve(json(200, [READ(1), READ(2)]));
        }),
    });
    renderApp("/wishlist");

    await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(screen.getByRole("button", { name: "Refresh from Audible" }));

    expect(await screen.findByRole("button", { name: "Refreshing…" })).toBeDisabled();
    expect(screen.getByText("Reading the wishlists from Audible…")).toBeInTheDocument();
    release();
    expect(await screen.findByRole("button", { name: "Refresh from Audible" })).toBeEnabled();
    expect(api.sent("POST /api/wishlist/refresh")).toEqual([{}]);
  });

  it("says which account's wishlist could not be read, and shows the rest", async () => {
    fakeApi(
      signedIn({
        "GET /api/status": ok(status({ accounts: [UK, US] })),
        "GET /api/wishlist": listing([OMW], [READ(1), READ(2, { error: "NetworkError: down", error_at: "x" })]),
      }),
    );
    renderApp("/wishlist");

    expect(await screen.findByText("Alex (US)'s wishlist could not be read.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Old Man's War" })).toBeInTheDocument();
  });

  it("filters by account, with a word when that one is empty", async () => {
    wishlist([OMW]);
    renderApp("/wishlist");

    await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "2");

    expect(await screen.findByRole("heading", { name: "Alex (US)'s wishlist is empty" })).toBeInTheDocument();
    expect(screen.getByText("The other accounts have 1 book on theirs.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all accounts" }));
    expect(await screen.findByRole("article", { name: "Old Man's War" })).toBeInTheDocument();
  });

  it("says when every wishlist is empty", async () => {
    wishlist([], {}, [UK]);
    renderApp("/wishlist");

    expect(await screen.findByRole("heading", { name: "Your wishlist is empty" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Search the catalogue" })).toHaveAttribute("href", "/search");
  });

  it("points to Accounts when no account is signed in", async () => {
    wishlist([], {}, [account({ needs_login: true })]);
    renderApp("/wishlist");

    expect(await screen.findByRole("heading", { name: "No wishlist to show" })).toBeInTheDocument();
  });

  it("opens a book's panel for its account's marketplace", async () => {
    wishlist([OMW], {
      "GET /api/catalog/books/B06XNYQJTX": ok({ ...catalogBook(), description: "", description_html: "" }),
    });
    renderApp("/wishlist");

    const omw = await screen.findByRole("article", { name: "Old Man's War" });
    await userEvent.click(within(omw).getByRole("button", { name: "Old Man's War" }));

    const panel = await screen.findByRole("dialog", { name: "Old Man's War" });
    await waitFor(() => expect(within(panel).getByText("Macmillan Digital Audio")).toBeInTheDocument());
  });
});
