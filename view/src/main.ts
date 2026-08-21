// Entry point. The host page sets `window.__FOE__` before this script runs
// and provides a `<div id="app">`; without one the bundle mounts into body.

import { App } from "./app.js";
import { loadSettings } from "./chrome.js";
import { h } from "./dom.js";
import { start } from "./source.js";
import type { Config } from "./source.js";

declare global {
  interface Window {
    __FOE__?: Config;
  }
}

function boot(): void {
  const root = document.getElementById("app") ?? document.body;
  loadSettings(root);
  const config = window.__FOE__;
  if (!config || (config.mode !== "static" && config.mode !== "live")) {
    root.replaceChildren(h("div", { class: "empty" }, "window.__FOE__ is missing or names no mode; nothing to show"));
    return;
  }
  const app = new App(root, config.mode === "live");
  start(config, app);
}

boot();
