import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UpdateNotice } from "./UpdateNotice";
import { setUpdates, watchUpdates } from "./updates";

/** An EventTarget standing in for a ServiceWorker, a registration or the container. */
class Fake extends EventTarget {
  fire(type: string) {
    this.dispatchEvent(new Event(type));
  }
}

class FakeWorker extends Fake {
  state: ServiceWorkerState = "installing";
  postMessage = vi.fn();
  become(state: ServiceWorkerState) {
    this.state = state;
    this.fire("statechange");
  }
}

class FakeRegistration extends Fake {
  installing: FakeWorker | null = null;
  waiting: FakeWorker | null = null;
  update = vi.fn(async () => undefined);
  /** A new build found: it installs and then waits. */
  found(worker = new FakeWorker()) {
    this.installing = worker;
    this.fire("updatefound");
    worker.become("installed");
    this.installing = null;
    this.waiting = worker;
    return worker;
  }
}

function setup({ controlled = true, waiting = null as FakeWorker | null } = {}) {
  const registration = new FakeRegistration();
  registration.waiting = waiting;
  const container = Object.assign(new Fake(), {
    controller: controlled ? new FakeWorker() : null,
    register: vi.fn(async () => registration),
  });
  const reload = vi.fn();
  const doc = Object.assign(new Fake(), { visibilityState: "visible" });
  const updates = watchUpdates(container as unknown as ServiceWorkerContainer, "https://books.example/sw.js", {
    reload,
    every: 1000,
    doc: doc as unknown as Document,
  });
  const registered = async () => {
    await act(async () => {
      await Promise.resolve();
    });
  };
  return { registration, container, reload, doc, updates, registered };
}

afterEach(() => {
  setUpdates(null);
  vi.useRealTimers();
});

describe("watchUpdates", () => {
  it("registers the worker and offers a new build once it waits", async () => {
    const { container, registration, updates, registered } = setup();
    await registered();
    expect(container.register).toHaveBeenCalledWith("https://books.example/sw.js");
    expect(updates.available()).toBe(false);

    const listener = vi.fn();
    updates.subscribe(listener);
    registration.found();

    expect(updates.available()).toBe(true);
    expect(listener).toHaveBeenCalled();
  });

  it("offers nothing on the first install, when no worker controlled the page", async () => {
    const { registration, updates, registered } = setup({ controlled: false });
    await registered();

    registration.found();

    expect(updates.available()).toBe(false);
  });

  it("offers a build another window found before this page opened", async () => {
    const waiting = new FakeWorker();
    waiting.state = "installed";
    const { updates, registered } = setup({ waiting });
    await registered();

    expect(updates.available()).toBe(true);
  });

  it("moves to the new build only when asked, then reloads once it has taken over", async () => {
    const { registration, container, reload, updates, registered } = setup();
    await registered();
    const worker = registration.found();

    // Another window's update taking over does not reload this page
    container.fire("controllerchange");
    expect(reload).not.toHaveBeenCalled();

    updates.apply();
    expect(worker.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
    expect(reload).not.toHaveBeenCalled();

    container.fire("controllerchange");
    expect(reload).toHaveBeenCalledOnce();
  });

  it("only reloads when the new build was already taken up elsewhere", async () => {
    const { registration, reload, updates, registered } = setup();
    await registered();
    const worker = registration.found();
    worker.become("activated");

    updates.apply();

    expect(worker.postMessage).not.toHaveBeenCalled();
    expect(reload).toHaveBeenCalledOnce();
  });

  it("asks for a new build every so often and when the page is shown again", async () => {
    vi.useFakeTimers();
    const { registration, doc, registered } = setup();
    await registered();

    vi.advanceTimersByTime(1000);
    expect(registration.update).toHaveBeenCalledTimes(1);

    doc.visibilityState = "hidden";
    doc.fire("visibilitychange");
    expect(registration.update).toHaveBeenCalledTimes(1);
    doc.visibilityState = "visible";
    doc.fire("visibilitychange");
    expect(registration.update).toHaveBeenCalledTimes(2);
  });

  it("leaves the app an ordinary page when the worker cannot be registered", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const container = Object.assign(new Fake(), {
      controller: null,
      register: vi.fn(async () => {
        throw new DOMException("insecure", "SecurityError");
      }),
    });

    const updates = watchUpdates(container as unknown as ServiceWorkerContainer, "sw.js", { reload: vi.fn() });
    await act(async () => {
      await Promise.resolve();
    });

    expect(updates.available()).toBe(false);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("UpdateNotice", () => {
  function fakeUpdates(available: boolean) {
    return { available: () => available, subscribe: () => () => undefined, apply: vi.fn<() => void>() };
  }

  it("says nothing while no new build waits, or without a service worker", () => {
    const { container } = render(<UpdateNotice />);
    expect(container).toBeEmptyDOMElement();

    setUpdates(fakeUpdates(false));
    render(<UpdateNotice />);
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  it("offers the new build, and Reload takes it up", async () => {
    const updates = fakeUpdates(true);
    setUpdates(updates);
    render(<UpdateNotice />);

    expect(screen.getByRole("status")).toHaveTextContent("Update available");
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));

    expect(updates.apply).toHaveBeenCalledOnce();
  });

  it("can be put aside for this page", async () => {
    setUpdates(fakeUpdates(true));
    render(<UpdateNotice />);

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  it("appears when a new build arrives while the page is open", () => {
    let available = false;
    const listeners = new Set<() => void>();
    setUpdates({
      available: () => available,
      subscribe: (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      apply: vi.fn(),
    });
    render(<UpdateNotice />);
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();

    act(() => {
      available = true;
      for (const listener of listeners) listener();
    });

    expect(screen.getByText("Update available")).toBeInTheDocument();
  });
});
