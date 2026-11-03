import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

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
