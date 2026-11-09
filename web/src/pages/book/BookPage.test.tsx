import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Status } from "../../api/queries";
import { fail, fakeApi, ok, type Reply, signedIn, status } from "../../test/fakeApi";
import { account, type Book, book } from "../../test/fixtures";
import { renderApp } from "../../test/render";

const UK = account();

const DUNE = book({
  id: 7,
  title: "One Word Kill",
  subtitle: "Impossible Times, Book 1",
  authors: ["Mark Lawrence"],
  narrators: ["Joe Jameson"],
  primary_series: { title: "Impossible Times", sequence: "1" },
  length: 426,
  release_date: "2019-05-09",
  date_added: "2026-10-28T14:00:00Z",
  genres: ["Science Fiction", "Time Travel"],
  percent_complete: 42,
  status: "downloaded",
  attempts: 1,
  last_attempt_at: "2026-10-28T14:06:00Z",
  downloaded_at: "2026-10-28T14:19:00Z",
  encoding_format: "m4b",
  file_path: "/app/audiobooks/Mark Lawrence/One Word Kill.m4b",
  cover_path: "/app/audiobooks/Mark Lawrence/One Word Kill_cover.jpg",
});

/** A run downloading the book `bookId`, 243 MB of 405 MB in. */
function running(bookId: number): Status {
  return status({
    accounts: [UK],
    scheduler: { enabled: true, interval_minutes: 360, next_run_at: null, running: true },
    current_run: {
      run_id: 148,
      started_at: "2026-11-02T14:00:00+00:00",
      stage: "downloading",
      account: null,
      book: { id: bookId, asin: "B0", title: "One Word Kill" },
      full_refresh: false,
      books_done: 2,
      books_total: 10,
      transfer: { desc: "One Word Kill", bytes: 243e6, total: 405e6 },
    },
  });
}

function page(theBook: Book | null = DUNE, extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/status": ok(status({ accounts: [UK] })),
      "GET /api/settings": ok({ max_attempts: 3 }),
      [`GET /api/books/${theBook?.id ?? 7}`]: theBook ? ok(theBook) : fail(404, "Book not found"),
      ...extra,
    }),
  );
}

describe("Book detail", () => {
  it("shows the book's details, downloads and files", async () => {
    page();
    renderApp("/library/7");

    expect(await screen.findByRole("heading", { level: 1, name: "One Word Kill" })).toBeInTheDocument();
    expect(screen.getByText("Impossible Times, Book 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mark Lawrence" })).toHaveAttribute("href", "/library?q=Mark%20Lawrence");
    expect(screen.getByText(/Narrated by Joe Jameson/)).toBeInTheDocument();
    expect(screen.getByText("Impossible Times #1")).toBeInTheDocument();
    expect(screen.getByText("7h 6m")).toBeInTheDocument();
    expect(screen.getByText(/May.*2019/)).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("Alex (UK)")).toBeInTheDocument();

    const download = screen.getByRole("region", { name: "Download" });
    expect(await within(download).findByText("1 of 3")).toBeInTheDocument();
    expect(within(download).getByText("M4B (AAC, original)")).toBeInTheDocument();
    const files = screen.getByRole("region", { name: "Files on disk" });
    expect(within(files).getByText("/app/audiobooks/Mark Lawrence/One Word Kill.m4b")).toBeInTheDocument();
    expect(within(files).getByText("No companion PDF")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Redownload" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Retry download" })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Library / One Word Kill");
  });

  it("is the book 404 for a book the API does not have, or an id that is not one", async () => {
    page(null);
    renderApp("/library/7");
    expect(await screen.findByRole("heading", { name: "This book is not in your library" })).toBeInTheDocument();
  });

  it("is the book 404 for an address that is no id at all", async () => {
    const api = page();
    renderApp("/library/abc");
    expect(await screen.findByRole("heading", { name: "This book is not in your library" })).toBeInTheDocument();
    expect(api.calls.some((call) => call.path.startsWith("/api/books/"))).toBe(false);
  });

  it("offers a retry and the error for a failed book", async () => {
    const api = page(book({ id: 7, status: "failed", attempts: 3, last_error: "ffmpeg exited with status 1" }), {
      "POST /api/books/7/retry": ok({ status: "queued", removed: [] }),
    });
    renderApp("/library/7");
    const user = userEvent.setup();

    expect(await screen.findByText("Gave up after 3 attempts")).toBeInTheDocument();
    expect(screen.getByText("ffmpeg exited with status 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Redownload" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry download" }));
    expect(await screen.findByText("Queued for the next run.")).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path === "/api/books/7/retry")).toHaveLength(1);
  });

  it("confirms before deleting the files", async () => {
    const api = page(DUNE, {
      "POST /api/books/7/delete-files": ok({ status: "deleted", removed: ["/a.m4b", "/a_cover.jpg"] }),
    });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Delete files" }));
    const dialog = screen.getByRole("dialog", { name: "Delete the files for One Word Kill?" });
    await user.click(within(dialog).getByRole("button", { name: "Keep files" }));
    expect(api.calls.some((call) => call.path === "/api/books/7/delete-files")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Delete files" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete files" }));
    expect(await screen.findByText(/Deleted the files\. The book is unmonitored/)).toBeInTheDocument();
  });

  it("says when a run took the book meanwhile", async () => {
    page(DUNE, { "POST /api/books/7/redownload": fail(409, "A run is downloading this book") });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Redownload" }));
    await user.click(screen.getByRole("button", { name: "Delete and redownload" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A run is downloading this book");
  });

  it("confirms a redownload, which deletes the files first", async () => {
    const api = page(DUNE, { "POST /api/books/7/redownload": ok({ status: "queued", removed: ["/a.m4b"] }) });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Redownload" }));
    const dialog = screen.getByRole("dialog", { name: "Redownload One Word Kill?" });
    expect(dialog).toHaveTextContent("The files are deleted now");
    await user.click(within(dialog).getByRole("button", { name: "Keep files" }));
    expect(api.calls.some((call) => call.path === "/api/books/7/redownload")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Redownload" }));
    await user.click(screen.getByRole("button", { name: "Delete and redownload" }));
    expect(await screen.findByText(/queued for the next run/)).toBeInTheDocument();
  });

  it("offers no retry for a title Audible withdrew, but does for a refused licence", async () => {
    const api = page(book({ id: 7, status: "unavailable", is_consumable: false }));
    renderApp("/library/7");
    expect(await screen.findByText("Audible withdrew this title")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry download" })).not.toBeInTheDocument();

    api.routes["GET /api/books/7"] = ok(book({ id: 7, status: "unavailable", is_consumable: true }));
    renderApp("/library/7");
    expect(await screen.findByRole("button", { name: "Retry download" })).toBeInTheDocument();
  });

  it("links an author listed twice twice", async () => {
    page(book({ ...DUNE, authors: ["Mark Lawrence", "Mark Lawrence"] }));
    renderApp("/library/7");
    expect(await screen.findAllByRole("link", { name: "Mark Lawrence" })).toHaveLength(2);
  });

  it("points a refresh refused for want of a sign-in at Accounts, and words Audible not answering", async () => {
    const api = page(DUNE, { "POST /api/books/7/refresh": fail(409, "Account 1 has no credentials") });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Refresh from Audible" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Alex (UK) needs a sign-in before the book can be read from Audible.");
    expect(within(alert).getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/accounts");

    api.routes["POST /api/books/7/refresh"] = fail(502, "Audible did not answer: timed out");
    await user.click(screen.getByRole("button", { name: "Refresh from Audible" }));
    expect(await screen.findByText("Audible did not answer. Try again in a minute.")).toBeInTheDocument();
  });

  it("switches monitoring at once, and notes an unmonitored book", async () => {
    // The service's copy of the book, which the PATCH changes
    let stored = book({ ...DUNE, monitored: false });
    let answer: () => void = () => undefined;
    const api = page(stored, {
      "GET /api/books/7": () => Response.json(stored),
      "PATCH /api/books/7": () =>
        new Promise<Response>((resolve) => {
          answer = () => {
            stored = { ...stored, monitored: true };
            resolve(Response.json(stored));
          };
        }),
    });
    renderApp("/library/7");
    const user = userEvent.setup();

    expect(await screen.findByText("Not monitored")).toBeInTheDocument();
    expect(screen.getByText(/This book is not monitored/)).toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "Monitored" }));
    // Before the service has answered
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Monitored" })).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.queryByText("Not monitored")).not.toBeInTheDocument();
    expect(api.sent("PATCH /api/books/7")).toEqual([{ monitored: true }]);
    answer();
    await waitFor(() => expect(api.calls.filter((call) => call.path === "/api/books/7").length).toBeGreaterThan(2));
    expect(screen.getByRole("switch", { name: "Monitored" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("monitoring", () => {
  it("puts the switch back and says so when the service refuses", async () => {
    page(DUNE, { "PATCH /api/books/7": fail(500, "database is locked") });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("switch", { name: "Monitored" }));
    expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent(
      "Could not stop monitoring the book",
    );
    expect(screen.getByRole("switch", { name: "Monitored" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("a book being downloaded", () => {
  it("shows the run's progress, offers to cancel it and holds the actions", async () => {
    const api = page(book({ ...DUNE, status: "downloading" }), {
      "GET /api/status": ok(running(7)),
      "POST /api/sync/cancel": ok({ status: "cancelling" }),
    });
    renderApp("/library/7");
    const user = userEvent.setup();

    expect(await screen.findByText("Downloading in run #148")).toBeInTheDocument();
    expect(screen.getByText("Book 3 of 10 this run")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Download progress" })).toHaveAttribute("aria-valuenow", "60");
    expect(screen.getByText("243 MB of 405 MB")).toBeInTheDocument();
    for (const name of ["Redownload", "Delete files", "Refresh from Audible"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByRole("switch", { name: "Monitored" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(api.calls.some((call) => call.path === "/api/sync/cancel")).toBe(true);
  });

  it("notes a book marked downloading that no run holds", async () => {
    page(book({ ...DUNE, status: "downloading", last_attempt_at: "2026-11-02T08:14:00Z" }), {
      "GET /api/status": ok(running(99)),
    });
    renderApp("/library/7");

    expect(await screen.findByText(/Marked as downloading since [^T]*; no run is working on it\./)).toBeInTheDocument();
    expect(screen.queryByText(/Downloading in run/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redownload" })).toBeDisabled();
  });

  it("says nothing about a stale claim before the status has answered", async () => {
    let answer: () => void = () => undefined;
    page(book({ ...DUNE, status: "downloading" }), {
      "GET /api/status": () =>
        new Promise<Response>((resolve) => {
          answer = () => resolve(Response.json(running(7)));
        }),
    });
    renderApp("/library/7");

    expect(await screen.findByRole("heading", { level: 1, name: "One Word Kill" })).toBeInTheDocument();
    expect(screen.queryByText(/no run is working on it/)).not.toBeInTheDocument();
    answer();
    expect(await screen.findByText("Downloading in run #148")).toBeInTheDocument();
    expect(screen.queryByText(/no run is working on it/)).not.toBeInTheDocument();
  });

  it("does not call the book stale while it is read again after the run moves on", async () => {
    let stored: Book = book({ ...DUNE, status: "downloading" });
    let answer: () => void = () => undefined;
    let held = false;
    const api = page(stored, {
      "GET /api/status": ok(running(7)),
      "GET /api/books/7": () => {
        if (!held) return Response.json(stored);
        return new Promise<Response>((resolve) => {
          answer = () => resolve(Response.json(stored));
        });
      },
    });
    const { client } = renderApp("/library/7");
    expect(await screen.findByText("Downloading in run #148")).toBeInTheDocument();

    // The run finishes this book and takes up the next; the read of this one is held
    held = true;
    stored = { ...stored, status: "downloaded" };
    api.routes["GET /api/status"] = ok(running(8));
    await client.refetchQueries({ queryKey: ["status"] });
    await waitFor(() => expect(screen.queryByText("Downloading in run #148")).not.toBeInTheDocument());
    expect(screen.queryByText(/no run is working on it/)).not.toBeInTheDocument();

    answer();
    await waitFor(() => expect(screen.getByRole("button", { name: "Redownload" })).toBeEnabled());
    expect(screen.queryByText(/no run is working on it/)).not.toBeInTheDocument();
  });
});

describe("monitoring, one change at a time", () => {
  it("holds the switch until its change is answered", async () => {
    let answer: () => void = () => undefined;
    page(DUNE, {
      "PATCH /api/books/7": () =>
        new Promise<Response>((resolve) => {
          answer = () => resolve(Response.json({ ...DUNE, monitored: false }));
        }),
    });
    renderApp("/library/7");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("switch", { name: "Monitored" }));
    expect(screen.getByRole("switch", { name: "Monitored" })).toBeDisabled();
    answer();
    await waitFor(() => expect(screen.getByRole("switch", { name: "Monitored" })).toBeEnabled());
  });
});
