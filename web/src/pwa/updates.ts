/**
 * The page's side of the service worker: register it, notice a new build waiting, and
 * move to that build only when someone asks.
 *
 * A new build installs and then waits (see `sw.js`), so an open page keeps the build it
 * opened with. When one is waiting, `available` is true and the app says Update
 * available. `apply` tells it to take over and reloads once it has. The page reloads
 * only when this page asked: the first install also takes control of the page, and
 * another window's update does too.
 */
import { useSyncExternalStore } from "react";

/** How often an open page asks whether there is a new build. */
export const CHECK_EVERY_MS = 60 * 60 * 1000;

export type Updates = {
  available: () => boolean;
  subscribe: (listener: () => void) => () => void;
  /** Take up the waiting build and reload into it. */
  apply: () => void;
};

type Options = {
  reload?: () => void;
  every?: number;
  doc?: Document;
};

export function watchUpdates(
  container: ServiceWorkerContainer,
  scriptUrl: string,
  { reload = () => location.reload(), every = CHECK_EVERY_MS, doc = document }: Options = {},
): Updates {
  let waiting: ServiceWorker | null = null;
  let asked = false;
  const listeners = new Set<() => void>();
  const offer = (worker: ServiceWorker) => {
    waiting = worker;
    for (const listener of listeners) listener();
  };

  container.addEventListener("controllerchange", () => {
    if (asked) reload();
  });

  container
    .register(scriptUrl)
    .then((registration) => {
      // A worker already waiting when this page opened: another window found it first.
      // With no controller, this is the first install, and nothing is out of date
      if (registration.waiting && container.controller) offer(registration.waiting);
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed" && container.controller) offer(worker);
        });
      });
      const check = () => {
        registration.update().catch(() => {
          // Offline or the service down: the next check will do
        });
      };
      setInterval(check, every);
      doc.addEventListener("visibilitychange", () => {
        if (doc.visibilityState === "visible") check();
      });
    })
    .catch((error: unknown) => {
      // Not fatal: the app works as an ordinary page
      console.warn("The service worker could not be registered", error);
    });

  return {
    available: () => waiting !== null,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    apply: () => {
      // A worker that has left "installed" was taken up by another window, or replaced:
      // this page only needs to reload
      if (waiting?.state !== "installed") {
        reload();
        return;
      }
      asked = true;
      waiting.postMessage({ type: "SKIP_WAITING" });
    },
  };
}

let current: Updates | null = null;

/**
 * Register the service worker, in a build and where the browser has them. A browser
 * only has them on https or on localhost, so a plain http address on a LAN stays an
 * ordinary page. `sw.js` is where `serviceWorker.ts` writes it, beside `index.html`, so
 * its scope is the whole app.
 */
export function startUpdates(): void {
  if (!("serviceWorker" in navigator)) return;
  current = watchUpdates(navigator.serviceWorker, new URL("sw.js", document.baseURI).href);
}

/** Only for tests. */
export function setUpdates(updates: Updates | null): void {
  current = updates;
}

const never = () => () => undefined;

/** Whether a new build is waiting, and the way to take it up. */
export function useUpdate(): { available: boolean; apply: () => void } {
  const updates = current;
  const available = useSyncExternalStore(updates?.subscribe ?? never, () => updates?.available() ?? false);
  return { available, apply: () => updates?.apply() };
}
