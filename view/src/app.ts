// Application state and wiring: one fold, conversation view, and raw view
// per episode; one tree; one optional diff. Views are patched, never rebuilt,
// when events arrive, and the tree pane redraws only when its digest changes.

import { Topbar } from "./chrome.js";
import type { ConnectionState, Crumb } from "./chrome.js";
import { clear, h } from "./dom.js";
import { EpisodeFold } from "./fold.js";
import type { Patch, Summary } from "./fold.js";
import { buildTree, flatten, sharedPrefix } from "./lineage.js";
import { ConversationView } from "./render/conversation.js";
import { DiffView, renderNoDiff } from "./render/diff.js";
import { RawView } from "./render/raw.js";
import { renderInfo, renderTree } from "./render/tree.js";
import type { Sink } from "./source.js";
import type { LogEvent } from "./types.js";

type Tab = "conversation" | "raw" | "diff";

interface EpisodeState {
  id: string;
  fold: EpisodeFold;
  conv: ConversationView;
  raw: RawView;
}

export class App implements Sink {
  private readonly episodes = new Map<string, EpisodeState>();
  private orderIds: string[] = [];
  private selected: string | null = null;
  private cursor: string | null = null;
  private compare: string[] = [];
  private tab: Tab = "conversation";
  private diff: DiffView | null = null;
  private diffKey = "";
  private sidebarScheduled = false;
  private treeDigest = "";
  private infoDigest = "";
  private connection: { state: ConnectionState; detail: string } = { state: "file", detail: "" };
  private readonly scrollMemory = new Map<HTMLElement, number>();

  private readonly topbar: Topbar;
  private readonly treeHost = h("div", { class: "tree-host" });
  private readonly infoHost = h("div");
  private readonly tabsBar: HTMLElement;
  private readonly views = h("div", { class: "views" });
  private readonly title = h("span", { class: "title" });

  constructor(root: HTMLElement, private readonly live: boolean) {
    this.topbar = new Topbar({ up: () => this.up(), select: (id) => this.select(id) });
    this.tabsBar = h(
      "div",
      { class: "tabs", role: "tablist" },
      this.tabButton("conversation", "conversation"),
      this.tabButton("raw", "raw events"),
      this.tabButton("diff", "diff"),
      this.title,
    );
    const treePane = h("div", { class: "pane-tree" }, h("h1", null, "episodes"), this.treeHost, this.infoHost);
    const mainPane = h("div", { class: "pane-main" }, this.tabsBar, this.views);
    const keys = h(
      "div",
      { class: "keys" },
      h("kbd", null, "j"),
      "/",
      h("kbd", null, "k"),
      " move · ",
      h("kbd", null, "enter"),
      " select · ",
      h("kbd", null, "c"),
      " compare · ",
      h("kbd", null, "/"),
      " filter · ",
      h("kbd", null, "1"),
      h("kbd", null, "2"),
      h("kbd", null, "3"),
      " tabs",
    );
    clear(root);
    root.classList.add("foe");
    root.append(this.topbar.el, treePane, mainPane, keys);
    document.addEventListener("keydown", (e) => this.onKey(e));
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => this.scheduleSidebar()).observe(this.treeHost);
    }
    this.connection = { state: live ? "reconnecting" : "file", detail: live ? "connecting" : "file" };
    this.renderSidebar();
    this.renderMain();
    this.renderStatus();
  }

  // Sink

  order(ids: string[]): void {
    this.orderIds = ids;
    for (const id of ids) this.episode(id);
    this.scheduleSidebar();
    if (this.selected === null && ids.length > 0) this.select(ids[0]!);
  }

  events(id: string, events: LogEvent[]): void {
    const state = this.episode(id);
    const patches: Patch[] = [];
    const accepted: LogEvent[] = [];
    for (const ev of events) {
      const before = state.fold.summary.lastSeq;
      let produced: Patch[];
      try {
        produced = state.fold.push(ev);
      } catch {
        // A malformed event must not take the viewer down; it still
        // appears in the raw table when the fold accepted its seq.
        produced = [];
      }
      if (state.fold.summary.lastSeq !== before) accepted.push(ev);
      patches.push(...produced);
    }
    if (accepted.length === 0) return;
    state.raw.add(accepted);
    state.conv.apply(patches);
    if (this.diff && (id === this.diff.a.id || id === this.diff.b.id)) this.diff.apply(id, patches);
    this.scheduleSidebar();
    this.renderStatus();
    if (this.selected === null) this.select(id);
  }

  status(state: ConnectionState, detail: string): void {
    this.connection = { state, detail };
    this.renderStatus();
  }

  // State changes

  select(id: string): void {
    if (!this.episodes.has(id)) return;
    this.selected = id;
    this.cursor = id;
    if (this.tab === "diff") this.tab = "conversation";
    this.renderSidebar();
    this.renderMain();
  }

  /** Moves the selection to the parent or fork origin of the selected episode. */
  up(): void {
    const s = this.selected ? this.episodes.get(this.selected)?.fold.summary : undefined;
    const parent = s ? (s.parentId ?? s.forkOrigin?.episodeId ?? null) : null;
    if (parent && this.episodes.has(parent)) this.select(parent);
  }

  toggleCompare(id: string): void {
    if (this.compare.includes(id)) {
      this.compare = this.compare.filter((x) => x !== id);
    } else {
      this.compare = [...this.compare.slice(-1), id];
    }
    if (this.compare.length === 2) this.tab = "diff";
    this.renderSidebar();
    this.renderMain();
  }

  setTab(tab: Tab): void {
    this.tab = tab;
    this.renderMain();
  }

  // Internals

  private episode(id: string): EpisodeState {
    let state = this.episodes.get(id);
    if (!state) {
      const ctx = { select: (target: string) => this.select(target) };
      state = {
        id,
        fold: new EpisodeFold(id, { stream: this.live }),
        conv: new ConversationView(ctx),
        raw: new RawView(),
      };
      this.episodes.set(id, state);
      if (!this.orderIds.includes(id)) this.orderIds = [...this.orderIds, id];
    }
    return state;
  }

  private summaries(): Summary[] {
    return [...this.episodes.values()].map((e) => e.fold.summary);
  }

  private summaryMap(): Map<string, Summary> {
    const map = new Map<string, Summary>();
    for (const e of this.episodes.values()) map.set(e.fold.summary.id, e.fold.summary);
    return map;
  }

  private scheduleSidebar(): void {
    if (this.sidebarScheduled) return;
    this.sidebarScheduled = true;
    requestAnimationFrame(() => {
      this.sidebarScheduled = false;
      this.renderSidebar();
    });
  }

  /** Chain of episodes from the root down to the selected one. */
  private lineage(): Crumb[] {
    const map = this.summaryMap();
    const chain: Crumb[] = [];
    let id = this.selected;
    const seen = new Set<string>();
    while (id && map.has(id) && !seen.has(id)) {
      seen.add(id);
      const s = map.get(id)!;
      chain.unshift({ id, label: s.name === s.id ? s.id : `${s.name} ${s.id}` });
      id = s.parentId ?? s.forkOrigin?.episodeId ?? null;
    }
    return chain;
  }

  private renderStatus(): void {
    const running = this.live ? this.summaries().filter((s) => s.lastSeq >= 0 && s.outcome === null).length : 0;
    this.topbar.status.set(this.connection.state, this.connection.detail, running);
  }

  private renderSidebar(): void {
    const summaries = this.summaries();
    const roots = buildTree(summaries, this.orderIds);
    const width = Math.max(160, this.treeHost.clientWidth - 16);
    const structural = summaries
      .map((s) => [s.id, s.name, s.parentId, s.forkOrigin?.episodeId, s.forkOrigin?.seq, s.outcome ? JSON.stringify(s.outcome) : "", s.lastSeq >= 0 ? 1 : 0].join(""))
      .join("");
    const treeDigest = [structural, this.selected, this.cursor, this.compare.join(","), width, this.orderIds.join(",")].join("");
    if (treeDigest !== this.treeDigest) {
      this.treeDigest = treeDigest;
      const tree = renderTree(
        roots,
        width,
        { selected: this.selected, cursor: this.cursor, compare: this.compare },
        { select: (id) => this.select(id), toggleCompare: (id) => this.toggleCompare(id) },
      );
      clear(this.treeHost);
      this.treeHost.appendChild(tree);
      const cursorEl = tree.querySelector<SVGGElement>(".node.cursor");
      if (cursorEl) cursorEl.scrollIntoView({ block: "nearest" });
    }
    const selected = this.selected ? this.episodes.get(this.selected) : undefined;
    const s = selected?.fold.summary ?? null;
    const infoDigest = s
      ? [s.id, s.lastSeq, s.modelCalls, s.retries, s.usage.input, s.usage.output, s.usage.cacheRead, s.outcome ? JSON.stringify(s.outcome) : "", s.roster.size, s.children.size, s.endTime].join("")
      : "";
    if (infoDigest !== this.infoDigest) {
      this.infoDigest = infoDigest;
      clear(this.infoHost);
      this.infoHost.appendChild(renderInfo(s));
    }
    this.topbar.setCrumbs(this.lineage());
  }

  private tabButton(tab: Tab, label: string): HTMLElement {
    return h("button", { class: "tab", type: "button", role: "tab", "data-tab": tab, onclick: () => this.setTab(tab) }, label);
  }

  private renderMain(): void {
    for (const b of this.tabsBar.querySelectorAll<HTMLElement>(".tab")) {
      const active = b.dataset.tab === this.tab;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    }
    const selected = this.selected ? this.episodes.get(this.selected) : undefined;
    this.title.textContent = selected ? `${selected.fold.summary.name} · ${selected.id}` : "";
    let next: HTMLElement;
    if (this.tab === "diff") {
      next = this.diffElement();
    } else if (!selected) {
      next = h("div", { class: "empty" }, this.live ? "waiting for episodes" : "no episodes in this log");
    } else {
      next = this.tab === "raw" ? selected.raw.el : selected.conv.el;
    }
    const current = this.views.firstElementChild as HTMLElement | null;
    if (current === next) return;
    if (current) {
      this.scrollMemory.set(current, current.scrollTop);
      current.remove();
    }
    this.views.appendChild(next);
    const remembered = this.scrollMemory.get(next);
    next.scrollTop = remembered === undefined ? next.scrollHeight : remembered;
  }

  private diffElement(): HTMLElement {
    if (this.compare.length < 2) {
      return renderNoDiff("mark two episodes with the compare box in the tree to see their shared prefix once and their suffixes side by side");
    }
    const [a, b] = this.compare as [string, string];
    const shared = sharedPrefix(a, b, this.summaryMap());
    const key = `${a}|${b}|${shared}`;
    if (this.diff && this.diffKey === key) return this.diff.el;
    this.diff = null;
    this.diffKey = key;
    if (shared === 0) return renderNoDiff(`${a} and ${b} share no fork prefix`);
    const ea = this.episodes.get(a);
    const eb = this.episodes.get(b);
    if (!ea || !eb) return renderNoDiff("one of the compared episodes is not loaded");
    const ctx = { select: (target: string) => this.select(target) };
    this.diff = new DiffView(
      { id: a, name: ea.fold.summary.name, rows: ea.fold.rows },
      { id: b, name: eb.fold.summary.name, rows: eb.fold.rows },
      shared,
      ctx,
    );
    return this.diff.el;
  }

  private onKey(e: KeyboardEvent): void {
    const target = e.target as HTMLElement | null;
    const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
    if (typing) {
      if (e.key === "Escape") (target as HTMLElement).blur();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const flat = flatten(buildTree(this.summaries(), this.orderIds)).map((f) => f.node.id);
    switch (e.key) {
      case "j":
      case "k": {
        if (flat.length === 0) return;
        const at = this.cursor ? flat.indexOf(this.cursor) : -1;
        const step = e.key === "j" ? 1 : -1;
        const nextIndex = at < 0 ? 0 : Math.min(flat.length - 1, Math.max(0, at + step));
        this.cursor = flat[nextIndex]!;
        this.renderSidebar();
        e.preventDefault();
        break;
      }
      case "Enter":
        if (this.cursor) this.select(this.cursor);
        e.preventDefault();
        break;
      case "c": {
        const id = this.cursor ?? this.selected;
        if (id) this.toggleCompare(id);
        e.preventDefault();
        break;
      }
      case "/": {
        this.setTab("raw");
        const selected = this.selected ? this.episodes.get(this.selected) : undefined;
        if (selected) selected.raw.focus();
        e.preventDefault();
        break;
      }
      case "1":
        this.setTab("conversation");
        break;
      case "2":
        this.setTab("raw");
        break;
      case "3":
        this.setTab("diff");
        break;
      default:
        break;
    }
  }
}
