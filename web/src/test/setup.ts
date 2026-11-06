import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// How long findBy and waitFor keep looking, 1 s by default. The first test of a file pays
// for the app's first render, which took longer than that on a busy CI runner and failed
// the Library's first test now and then. Only a test about to fail waits this long.
configure({ asyncUtilTimeout: 5000 });

// jsdom has the <dialog> element but not its methods: open and close by the attribute,
// as a browser does (focus trapping and the backdrop are the browser's, not tested here)
const dialog = HTMLDialogElement.prototype;
if (!dialog.showModal) {
  dialog.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  dialog.show = dialog.showModal;
  dialog.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute("open")) return;
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
}

// Nor matchMedia: nothing matches unless a test says otherwise
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});
