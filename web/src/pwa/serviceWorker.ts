/**
 * The build step behind the service worker. Once Vite has written the build, it writes
 * `dist/sw.js` from `src/pwa/sw.js`, adding the build's version and the files that
 * make up the shell.
 *
 * The version is a hash of every file in the build, so a new build is a new `sw.js`.
 * A changed `sw.js` is how the browser finds out there is something to offer.
 * Runs in Node, from vite.config.ts.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import type { Plugin } from "vite";

export const SERVICE_WORKER = "sw.js";
const TEMPLATE = join("src", "pwa", "sw.js");
const MARKER = /^const BUILD = .*\/\/ build:inject$/m;

/**
 * What the shell needs to open offline, as URLs relative to the app. That is
 * `index.html` (as `./`, the address the service answers it at, with its base path
 * written in), the scripts and styles, the manifest, the favicon and the icons. The rest
 * of `assets/`, the fonts mostly, is cached as it is used, since every subset of every
 * weight would be most of the download.
 */
export function precacheList(files: string[]): string[] {
  const list = files.filter(
    (file) =>
      (file.startsWith("assets/") && /\.(js|css)$/.test(file)) ||
      file === "manifest.webmanifest" ||
      file === "favicon.svg" ||
      (file.startsWith("icons/") && file.endsWith(".png")),
  );
  return ["./", ...list.sort()];
}

/** The template with the build's version and file list written into its marked line. */
export function injectBuild(template: string, version: string, precache: string[]): string {
  if (!MARKER.test(template)) {
    throw new Error(`${TEMPLATE} has no line ending "// build:inject" to write the build into`);
  }
  const build = JSON.stringify({ version, precache });
  return template.replace(MARKER, () => `const BUILD = ${build};`);
}

/** A hash of the files' names and contents: the same build, the same version. */
export function buildVersion(files: { path: string; content: Uint8Array }[]): string {
  const hash = createHash("sha256");
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    hash.update(file.path).update("\0").update(file.content).update("\0");
  }
  return hash.digest("hex").slice(0, 16);
}

function walk(dir: string, root: string = dir): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path, root);
    return [relative(root, path).split(sep).join("/")];
  });
}

/** The Vite plugin: writes `sw.js` into the build once everything else is there. */
export function serviceWorker(): Plugin {
  let root = "";
  let outDir = "";
  return {
    name: "audible-sync-service-worker",
    apply: "build",
    configResolved(config) {
      root = config.root;
      outDir = join(config.root, config.build.outDir);
    },
    closeBundle() {
      const files = walk(outDir).filter((file) => file !== SERVICE_WORKER && !file.endsWith(".map"));
      const version = buildVersion(files.map((path) => ({ path, content: readFileSync(join(outDir, path)) })));
      const template = readFileSync(join(root, TEMPLATE), "utf-8");
      writeFileSync(join(outDir, SERVICE_WORKER), injectBuild(template, version, precacheList(files)));
    },
  };
}
