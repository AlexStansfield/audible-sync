import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "../api/client";
import { StatusChip } from "./Chip";
import { Cover } from "./Cover";
import { ConfirmDialog } from "./Dialog";
import { ErrorCard } from "./ErrorCard";
import { ProgressBar } from "./ProgressBar";
import { Switch } from "./Switch";
import { SUCCESS_LIFETIME, ToastProvider, useToast } from "./Toasts";

function Buttons() {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast.success("Settings saved.")}>
        ok
      </button>
      <button
        type="button"
        onClick={() =>
          toast.error("Could not delete the files", new ApiError(409, "A run is downloading this book.", "", ""))
        }
      >
        bad
      </button>
    </>
  );
}

function Harness({ onConfirm }: { onConfirm: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <ConfirmDialog
      open={open}
      title="Delete the files for 3 books?"
      confirmLabel="Delete files for 3 books"
      onConfirm={onConfirm}
      onCancel={() => setOpen(false)}
    >
      <p>The books stay in the library, unmonitored.</p>
    </ConfirmDialog>
  );
}

describe("toasts", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("closes a success after 4 s and keeps an error until dismissed", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("ok"));
    fireEvent.click(screen.getByText("bad"));
    expect(screen.getByRole("status")).toHaveTextContent("Settings saved.");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not delete the files");
    expect(screen.getByRole("alert")).toHaveTextContent("A run is downloading this book.");

    act(() => vi.advanceTimersByTime(SUCCESS_LIFETIME));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts the newest on top, and a later toast does not restart an earlier one's timer", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("ok"));
    act(() => vi.advanceTimersByTime(SUCCESS_LIFETIME - 1000));
    fireEvent.click(screen.getByText("bad"));
    const region = screen.getByRole("region", { name: "Notifications" });
    expect(region.firstElementChild).toHaveAttribute("role", "alert");

    act(() => vi.advanceTimersByTime(1000));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("is a labelled dialog whose destructive button confirms", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);

    const dialog = screen.getByRole("dialog", { name: "Delete the files for 3 books?" });
    expect(dialog).toHaveAttribute("open");
    await userEvent.click(screen.getByRole("button", { name: "Delete files for 3 books" }));

    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("closes on Cancel and on Escape", async () => {
    const { unmount } = render(<Harness onConfirm={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    unmount();

    render(<Harness onConfirm={vi.fn()} />);
    // Escape on a modal <dialog> is its cancel event
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ErrorCard", () => {
  it("shows what failed, the API's detail, the request and Retry", async () => {
    const onRetry = vi.fn();
    render(
      <ErrorCard
        title="Could not load the run history"
        error={new ApiError(500, "database is locked", "GET", "/api/sync/runs")}
        onRetry={onRetry}
      />,
    );

    const card = screen.getByRole("alert");
    expect(card).toHaveTextContent("Could not load the run history");
    expect(card).toHaveTextContent("database is locked");
    expect(card).toHaveTextContent("GET /api/sync/runs · 500");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("says when nothing answered", () => {
    render(<ErrorCard title="Could not load" error={new NetworkError("GET", "/api/stats", null)} />);
    expect(screen.getByRole("alert")).toHaveTextContent("GET /api/stats · no response");
  });
});

describe("Switch", () => {
  it("is a switch that reports the opposite state", async () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Monitored" />);

    const control = screen.getByRole("switch", { name: "Monitored" });
    expect(control).toHaveAttribute("aria-checked", "false");
    await userEvent.click(control);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("ProgressBar", () => {
  it("reports its value, or none when the size is unknown", () => {
    const { rerender } = render(<ProgressBar label="Download" value={243} max={972} />);
    expect(screen.getByRole("progressbar", { name: "Download" })).toHaveAttribute("aria-valuenow", "25");

    rerender(<ProgressBar label="Download" value={null} />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  });
});

describe("StatusChip", () => {
  it("uses the one word for each status", () => {
    render(<StatusChip status="waiting_download" />);
    expect(screen.getByText("Waiting")).toBeInTheDocument();
  });
});

describe("Cover", () => {
  it("tries the next book's cover after one failed", () => {
    const { container, rerender } = render(<Cover bookId={1} size={84} />);
    fireEvent.error(container.querySelector("img") as HTMLImageElement);
    expect(container.querySelector("img")).toBeNull();

    rerender(<Cover bookId={2} size={84} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "api/books/2/cover");
  });
});
