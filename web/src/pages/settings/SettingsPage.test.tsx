import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type FakeApi, fail, fakeApi, json, ok, type Reply, signedIn } from "../../test/fakeApi";
import { counts, settings, system } from "../../test/fixtures";
import { renderApp } from "../../test/render";

const STATS = {
  accounts: [],
  books: counts({ downloaded: 4 }),
  bytes_on_disk: 1.64e9,
  last_run: null,
  next_run_at: null,
};

function page(extra: Record<string, Reply> = {}) {
  return fakeApi(
    signedIn({
      "GET /api/settings": ok(settings()),
      "GET /api/settings/api-token": ok({ last4: "Xk3f", source: "stored" }),
      "GET /api/system": ok(system()),
      "GET /api/stats": ok(STATS),
      ...extra,
    }),
  );
}

/** PUT /api/settings as the service does it: the saved settings with the update applied. */
function saving(initial = settings()) {
  let current = initial;
  return (request: Request) =>
    request.json().then((update) => {
      current = { ...current, ...update };
      return json(200, current);
    });
}

const statusReads = (api: FakeApi) =>
  api.calls.filter((call) => call.method === "GET" && call.path === "/api/status").length;

async function open(extra: Record<string, Reply> = {}) {
  const api = page(extra);
  const view = renderApp("/settings");
  await screen.findByRole("heading", { name: "Schedule" });
  return { api, ...view };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("Settings", () => {
  it("shows the saved settings, and nothing to save until something changes", async () => {
    await open();
    expect(screen.getByLabelText("Run every")).toHaveValue("6");
    expect(screen.getByLabelText("Unit")).toHaveValue("hours");
    expect(screen.getByLabelText("Books per run")).toHaveValue("20");
    expect(screen.getByLabelText("Stop after failures in a row")).toHaveValue("");
    expect(screen.getByRole("radio", { name: /M4B/ })).toBeChecked();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    // Bitrate is for Ogg Opus only
    expect(screen.queryByLabelText(/Bitrate/)).not.toBeInTheDocument();
  });

  it("saves only the fields that changed, an emptied limit as null", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    await user.clear(screen.getByLabelText("Books per run"));
    await user.click(screen.getByRole("switch", { name: "Download new purchases automatically" }));
    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Settings saved.");
    expect(api.sent("PUT /api/settings")).toEqual([{ max_download: null, auto_monitor_new: false }]);
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Books per run")).toHaveValue("");
  });

  it("switches the daily full refresh", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    const daily = screen.getByRole("switch", { name: "Refresh every book from Audible once a day" });
    expect(daily).toBeChecked();

    await user.click(daily);
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await screen.findByText("Settings saved.");
    expect(api.sent("PUT /api/settings")).toEqual([{ daily_full_refresh: false }]);
  });

  it("does not count an interval written in other units as a change", async () => {
    const user = userEvent.setup();
    await open();
    await user.selectOptions(screen.getByLabelText("Unit"), "minutes");
    expect(screen.getByLabelText("Run every")).toHaveValue("360");
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("Run every"));
    await user.type(screen.getByLabelText("Run every"), "90");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    expect(screen.getByText("The next run is worked out again when you save. At least 5 minutes.")).toBeInTheDocument();
  });

  it("turns the next run to due now when its time comes with the page left open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, now: Date.parse("2026-11-02T17:59:58Z") });
    try {
      await open();
      expect(await screen.findByText(/^Next run .*(18:00|6:00 PM)/)).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(3000));
      expect(screen.getByText("Next run due now. At least 5 minutes.")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the service's refusal under the field it names", async () => {
    const user = userEvent.setup();
    await open({ "PUT /api/settings": fail(422, "sync interval-minutes must be 5 or more, got 2") });
    await user.selectOptions(screen.getByLabelText("Unit"), "minutes");
    await user.clear(screen.getByLabelText("Run every"));
    await user.type(screen.getByLabelText("Run every"), "2");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    const field = screen.getByLabelText("Run every");
    await waitFor(() => expect(field).toHaveAttribute("aria-invalid", "true"));
    expect(field).toHaveAccessibleDescription("sync interval-minutes must be 5 or more, got 2");
    // Still unsaved, and editing the field clears the error
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    await user.type(field, "0");
    expect(field).not.toHaveAttribute("aria-invalid");
  });

  it("says a failure that names no field beside the form", async () => {
    const user = userEvent.setup();
    await open({ "PUT /api/settings": fail(500, "Internal Server Error") });
    await user.click(screen.getByRole("switch", { name: "Run on a schedule" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save the settings: Internal Server Error");
  });

  it("does not send what it can tell is wrong", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    await user.type(screen.getByLabelText("Attempts per book"), ".5");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByLabelText("Attempts per book")).toHaveAccessibleDescription("Enter a whole number.");
    expect(api.sent("PUT /api/settings")).toEqual([]);
  });

  it("refuses a limit that is not a number rather than reading it as no limit", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    for (const typed of ["5e", "-", "1,5"]) {
      fireEvent.change(screen.getByLabelText("Books per run"), { target: { value: typed } });
      await user.click(screen.getByRole("button", { name: "Save changes" }));
      expect(screen.getByLabelText("Books per run")).toHaveAccessibleDescription(
        "Enter a whole number, or leave it empty.",
      );
    }
    expect(api.sent("PUT /api/settings")).toEqual([]);
  });

  it("does not count a bitrate hidden by going back to M4B", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("radio", { name: /Ogg Opus/ }));
    fireEvent.change(screen.getByLabelText(/Bitrate/), { target: { value: "32" } });
    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /M4B/ }));
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
  });

  it("puts every field back on Discard", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("radio", { name: /Ogg Opus/ }));
    await user.clear(screen.getByLabelText("Audiobooks folder"));
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("radio", { name: /M4B/ })).toBeChecked();
    expect(screen.getByLabelText("Audiobooks folder")).toHaveValue("/audiobooks");
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
  });

  it("offers the bitrate for Ogg Opus, and warns when FFmpeg has no libopus", async () => {
    const user = userEvent.setup();
    await open({ "GET /api/system": ok(system({ libopus: false })) });
    await user.click(screen.getByRole("radio", { name: /Ogg Opus/ }));
    const slider = screen.getByLabelText(/Bitrate/);
    fireEvent.change(slider, { target: { value: "48" } });
    expect(screen.getByText("48 kbps")).toBeInTheDocument();
    expect(screen.getByText(/no libopus encoder, so Ogg Opus downloads will fail/)).toBeInTheDocument();
    expect(screen.getByText("/audiobooks/Andy Weir/Project Hail Mary/Project Hail Mary.oga")).toBeInTheDocument();
  });
});

describe("Settings: folders and naming", () => {
  it("previews a series book and a standalone one with the templates as typed", async () => {
    await open();
    expect(
      screen.getByText("/audiobooks/Mark Lawrence/Impossible Times/1 - One Word Kill/One Word Kill.m4b"),
    ).toBeInTheDocument();
    expect(screen.getByText("/audiobooks/Andy Weir/Project Hail Mary/Project Hail Mary.m4b")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Folder template"), {
      target: { value: "{author}/[{series} ({year})/]{title}" },
    });
    expect(
      screen.getByText("/audiobooks/Mark Lawrence/Impossible Times (2019)/One Word Kill/One Word Kill.m4b"),
    ).toBeInTheDocument();
  });

  it("says at once when a template uses a placeholder the service does not know", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    const filename = screen.getByLabelText("File name template");
    await user.type(filename, " {{reader}");
    expect(filename).toHaveAttribute("aria-invalid", "true");
    expect(filename).toHaveAccessibleDescription(/Unknown placeholder '\{reader\}'/);
    expect(screen.getByText("Fix the template to see where books would go.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(api.sent("PUT /api/settings")).toEqual([]);
  });

  it("inserts a placeholder where the caret is in the template last used", async () => {
    const user = userEvent.setup();
    await open();
    const filename = screen.getByLabelText("File name template") as HTMLInputElement;
    await user.click(filename);
    filename.setSelectionRange(0, 0);
    expect(screen.getByText("Insert into the file name template")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "{asin}" }));
    expect(filename).toHaveValue("{asin}{title}");
    await user.click(screen.getByRole("button", { name: "{year}" }));
    expect(filename).toHaveValue("{asin}{year}{title}");
  });
});

describe("Settings: notifications", () => {
  it("sends a test to the URL in the field and says how it went", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "POST /api/settings/webhook/test": ok({ status: "sent" }) });
    await user.type(screen.getByLabelText("Webhook URL"), "http://abs:13378/hook");
    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(await screen.findByText("Test event delivered.")).toBeInTheDocument();
    expect(api.sent("POST /api/settings/webhook/test")).toEqual([{ url: "http://abs:13378/hook" }]);

    api.routes["POST /api/settings/webhook/test"] = fail(502, "Webhook failed: Connection refused");
    await user.click(screen.getByRole("button", { name: "Send test" }));
    expect(await screen.findByText("Webhook failed: Connection refused")).toBeInTheDocument();
  });

  it("cannot test an emptied field, which would test the saved webhook instead", async () => {
    const user = userEvent.setup();
    await open({ "GET /api/settings": ok(settings({ webhook_url: "http://abs:13378/hook" })) });
    expect(screen.getByRole("button", { name: "Send test" })).toBeEnabled();
    await user.clear(screen.getByLabelText("Webhook URL"));
    expect(screen.getByRole("button", { name: "Send test" })).toBeDisabled();
  });
});

describe("Settings: API access", () => {
  it("shows the token's last four, and the new token once after confirming", async () => {
    const user = userEvent.setup();
    let last4 = "Xk3f";
    const { api } = await open({
      "GET /api/settings/api-token": () => json(200, { last4, source: "stored" }),
      "POST /api/settings/api-token/regenerate": () => {
        last4 = "oQxk";
        return json(200, { token: "r7Vq2LmX9pK4tW8sN1cB6yH3jF0dZ5aE-Gu1oQxk" });
      },
    });
    expect(await screen.findByText(/•+ Xk3f/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    const dialog = screen.getByRole("dialog", { name: "Regenerate the API token?" });
    await user.click(within(dialog).getByRole("button", { name: "Keep the current token" }));
    expect(api.calls.some((call) => call.path === "/api/settings/api-token/regenerate")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    await user.click(
      within(screen.getByRole("dialog", { name: "Regenerate the API token?" })).getByRole("button", {
        name: "Regenerate",
      }),
    );
    expect(await screen.findByText("r7Vq2LmX9pK4tW8sN1cB6yH3jF0dZ5aE-Gu1oQxk")).toBeInTheDocument();
    expect(screen.getByText(/Copy it now/)).toBeInTheDocument();
  });

  it("cannot regenerate a token set in the environment", async () => {
    await open({ "GET /api/settings/api-token": ok({ last4: "abcd", source: "env" }) });
    expect(await screen.findByRole("button", { name: "Regenerate" })).toBeDisabled();
    expect(screen.getByText(/to change it, change it there/)).toBeInTheDocument();
  });
});

describe("Settings: system", () => {
  it("shows the disk, versions, key and database", async () => {
    await open();
    expect(await screen.findByText("1.6 TB free of 4 TB")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Disk space used" })).toHaveAttribute("aria-valuenow", "60");
    expect(screen.getByText("About 3,902 more books fit at your average size of 410 MB.")).toBeInTheDocument();
    expect(screen.getByText("7.1.1")).toBeInTheDocument();
    expect(screen.getByText("/app/secrets/secret.key")).toBeInTheDocument();
    expect(screen.getByText("4.2 MB")).toBeInTheDocument();
  });

  it("warns when the disk is nearly full", async () => {
    await open({
      "GET /api/system": ok(
        system({ audiobook_folder: { path: "/audiobooks", free_bytes: 38e9, total_bytes: 4e12, low_space: true } }),
      ),
    });
    expect(await screen.findByText(/Space is low\. About 92 more books fit/)).toBeInTheDocument();
  });
});

describe("Settings: what applies at once", () => {
  it("switches debug logging straight away, apart from the form", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    await user.click(screen.getByRole("switch", { name: "Debug logging" }));
    await waitFor(() => expect(api.sent("PUT /api/settings")).toEqual([{ debug: true }]));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Debug logging" })).toBeChecked());
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
  });

  it("does not read the status and queue again for debug logging, as a saved schedule does", async () => {
    const user = userEvent.setup();
    const { api } = await open({ "PUT /api/settings": saving() });
    await waitFor(() => expect(statusReads(api)).toBeGreaterThan(0));
    const before = statusReads(api);
    await user.click(screen.getByRole("switch", { name: "Debug logging" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Debug logging" })).toBeChecked());
    expect(statusReads(api)).toBe(before);

    await user.click(screen.getByRole("switch", { name: "Run on a schedule" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText("Settings saved.");
    await waitFor(() => expect(statusReads(api)).toBe(before + 1));
  });

  it("saves one thing at a time: debug logging waits for the form, and the form for it", async () => {
    const user = userEvent.setup();
    let release = () => {};
    const respond = saving();
    await open({
      "PUT /api/settings": (request: Request) =>
        new Promise<Response>((resolve) => {
          release = () => resolve(respond(request));
        }),
    });
    await user.click(screen.getByRole("switch", { name: "Run on a schedule" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("switch", { name: "Debug logging" })).toBeDisabled();
    await act(async () => release());
    await screen.findByText("Settings saved.");
    expect(screen.getByRole("switch", { name: "Debug logging" })).toBeEnabled();

    await user.click(screen.getByRole("switch", { name: "Download new purchases automatically" }));
    await user.click(screen.getByRole("switch", { name: "Debug logging" }));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await act(async () => release());
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
  });

  it("keeps unsaved changes when debug logging is switched", async () => {
    const user = userEvent.setup();
    await open({ "PUT /api/settings": saving() });
    await user.clear(screen.getByLabelText("Books per run"));
    await user.click(screen.getByRole("switch", { name: "Debug logging" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Debug logging" })).toBeChecked());
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    expect(screen.getByLabelText("Books per run")).toHaveValue("");
  });

  it("applies and remembers the theme in this browser", async () => {
    const user = userEvent.setup();
    await open();
    expect(screen.getByRole("radio", { name: "System" })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem("audible-sync.theme")).toBe("light");
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument();
  });
});

describe("Settings: leaving with unsaved changes", () => {
  it("asks first, and stays or leaves as told", async () => {
    const user = userEvent.setup();
    const { router } = await open();
    await user.click(screen.getByRole("switch", { name: "Run on a schedule" }));

    await act(() => router.navigate("/library"));
    const dialog = screen.getByRole("dialog", { name: "Leave without saving?" });
    await user.click(within(dialog).getByRole("button", { name: "Stay on this page" }));
    expect(router.state.location.pathname).toBe("/settings");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();

    await act(() => router.navigate("/library"));
    await user.click(
      within(screen.getByRole("dialog", { name: "Leave without saving?" })).getByRole("button", {
        name: "Leave without saving",
      }),
    );
    await waitFor(() => expect(router.state.location.pathname).toBe("/library"));
  });

  it("does not ask on the way to the sign-in: Sign out with unsaved changes signs out", async () => {
    const user = userEvent.setup();
    const { router } = await open({ "POST /api/auth/logout": () => new Response(null, { status: 204 }) });
    await user.click(screen.getByRole("switch", { name: "Run on a schedule" }));
    await user.click(screen.getAllByRole("button", { name: "Sign out" })[0] as HTMLElement);
    // To the sign-in, which (the fake still answering /me) moves on to the Dashboard
    await waitFor(() => expect(router.state.location.pathname).not.toBe("/settings"));
    expect(screen.queryByRole("dialog", { name: "Leave without saving?" })).not.toBeInTheDocument();
  });

  it("does not ask when nothing changed", async () => {
    const { router } = await open();
    await act(() => router.navigate("/library"));
    expect(router.state.location.pathname).toBe("/library");
  });
});
