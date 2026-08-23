// Application state and wiring: one fold, conversation view, and raw view
// per episode; one tree; one optional diff. Views are patched, never rebuilt,
// when events arrive, and the tree pane redraws only when its digest changes.

import { readCausality } from "./causality.js";
import type { CausalityEpisode, ConversationScope } from "./causality.js";
import { Topbar, currentFontScale, onSettingsChange } from "./chrome.js";
import type { ConnectionState, Crumb } from "./chrome.js";
import { clear, h } from "./dom.js";
import { EpisodeFold } from "./fold.js";
import type { Patch, Summary } from "./fold.js";
import { buildTree, flatten, sharedPrefix } from "./lineage.js";
import type { TreeNode } from "./lineage.js";
import { loadPanes, onPanesChange, rowGrip, setTrajectoryHeight, sidebarGrip } from "./panes.js";
import { ConversationView } from "./render/conversation.js";
import { DiffView, renderNoDiff } from "./render/diff.js";
import { RawView } from "./render/raw.js";
import { renderScope } from "./render/scoped.js";
import { StatisticsView } from "./render/statistics.js";
import { TrajectoryView } from "./render/trajectory.js";
import { renderInfo, renderTree } from "./render/tree.js";
import { WorkflowView } from "./render/workflow.js";
import type { Sink } from "./source.js";
import type { StatisticsEpisode } from "./statistics.js";
import type { TrajectoryEpisode } from "./trajectory.js";
import type { LogEvent } from "./types.js";
import { declaredWorkflow, readWorkflow } from "./workflow.js";

type Tab = "conversation" | "raw" | "diff" | "workflow" | "statistics";

/** The tab each digit selects, which is also the order of the tab bar. */
const TAB_KEYS: Tab[] = ["conversation", "raw", "diff", "workflow", "statistics"];

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
  /** The node of the causality figure the conversation is scoped to. */
  private scope: ConversationScope | null = null;
  private sidebarScheduled = false;
  private treeDigest = "";
  private infoDigest = "";
  private connection: { state: ConnectionState; detail: string } = { state: "file", detail: "" };
  private readonly scrollMemory = new Map<HTMLElement, number>();

  private readonly topbar: Topbar;
  private readonly trajectory: TrajectoryView;
  private readonly workflow: WorkflowView;
  private readonly statistics: StatisticsView;
  private readonly treeHost = h("div", { class: "tree-host" });
  private readonly infoHost = h("div", { class: "details-host" });
  private readonly tabsBar: HTMLElement;
  private readonly views = h("div", { class: "views" });
  private readonly title = h("span", { class: "title" });

  constructor(root: HTMLElement, private readonly live: boolean) {
    this.topbar = new Topbar({ up: () => this.up(), select: (id) => this.select(id) });
    this.trajectory = new TrajectoryView({
      select: (id) => this.select(id),
      reveal: (id, seq) => this.reveal(id, seq),
      scope: (scope) => this.setScope(scope),
    });
    this.workflow = new WorkflowView({ select: (id) => this.select(id) });
    this.statistics = new StatisticsView({ reveal: (id, seq) => this.reveal(id, seq) });
    this.tabsBar = h(
      "div",
      { class: "tabs", role: "tablist" },
      this.tabButton("conversation", "conversation"),
      this.tabButton("raw", "raw events"),
      this.tabButton("diff", "diff"),
      this.tabButton("workflow", "workflow"),
      this.tabButton("statistics", "statistics"),
      this.title,
    );
    const left = h(
      "section",
      { class: "column-left" },
      h("section", { class: "pane-tree", "aria-label": "episodes" }, h("div", { class: "pane-head" }, h("h2", null, "episodes")), this.treeHost),
      rowGrip("details", "height of the details pane"),
      h("section", { class: "pane-details", "aria-label": "episode details" }, h("div", { class: "pane-head" }, h("h2", null, "details")), this.infoHost),
    );
    const right = h(
      "section",
      { class: "column-right" },
      this.trajectory.el,
      rowGrip("trajectory", "height of the trajectory pane"),
      h("section", { class: "pane-main" }, this.tabsBar, this.views),
    );
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
      "–",
      h("kbd", null, "5"),
      " tabs · drag or arrow a grip to resize, double click to reset",
    );
    clear(root);
    root.classList.add("foe");
    root.append(this.topbar.el, left, sidebarGrip(), right, keys);
    loadPanes({ root, left, right });
    onPanesChange(() => this.trajectory.resized());
    // A figure is laid out in pixels, so a change of text size changes what
    // it should draw even when nothing resized the pane it sits in.
    onSettingsChange(() => {
      this.trajectory.resized();
      this.workflow.resized();
      this.statistics.resized();
      setTrajectoryHeight(this.trajectory.rowsHeight(), this.trajectory.chromeHeight(), currentFontScale());
    });
    document.addEventListener("keydown", (e) => this.onKey(e));
    if (typeof ResizeObserver !== "undefined") {
      const redraw = new ResizeObserver(() => {
        this.scheduleSidebar();
        this.trajectory.resized();
      });
      redraw.observe(this.treeHost);
      redraw.observe(this.trajectory.el);
      const tabs = new ResizeObserver(() => {
        this.workflow.resized();
        this.statistics.resized();
      });
      tabs.observe(this.views);
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
    if (this.tab === "workflow" && !this.declaresWorkflow(id)) this.tab = "conversation";
    this.renderSidebar();
    this.renderMain();
  }

  /** True when the episode's program declares a graph, which the tab needs. */
  private declaresWorkflow(id: string): boolean {
    const summary = this.episodes.get(id)?.fold.summary;
    return summary !== undefined && declaredWorkflow(summary.program) !== null;
  }

  /**
   * Selects an episode and brings its conversation to one log position.
   * A mark in the trajectory pane is clicked to reach the events it stands for.
   */
  reveal(id: string, seq: number): void {
    if (!this.episodes.has(id)) return;
    const changing = this.selected !== id || this.tab !== "conversation";
    this.tab = "conversation";
    this.select(id);
    const state = this.episodes.get(id)!;
    if (changing) requestAnimationFrame(() => state.conv.scrollToSeq(seq));
    else state.conv.scrollToSeq(seq);
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
    // The workflow tab exists only for an episode that declares a graph, so
    // its key does nothing for an episode that runs the free loop.
    if (tab === "workflow" && !(this.selected !== null && this.declaresWorkflow(this.selected))) return;
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
      .map((s) => [s.id, s.name, s.identity, s.parentId, s.forkOrigin?.episodeId, s.forkOrigin?.seq, s.outcome ? JSON.stringify(s.outcome) : "", s.lastSeq >= 0 ? 1 : 0].join(""))
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
    // The program name arrives with `episode/start`, after the first
    // selection is made, so the title is set on every sidebar redraw.
    this.title.textContent = s ? `${s.name} · ${s.id}` : "";
    this.trajectory.update(this.trajectoryEpisodes(roots), this.causalityEpisodes(roots), {
      selected: this.selected,
      cursor: this.cursor,
    });
    // The trajectory region opens at the height its rows need, so a run of
    // one episode does not open a region of mostly empty ground. A row is
    // as tall as its own channels, so the height follows the drawing rather
    // than the row count.
    setTrajectoryHeight(this.trajectory.rowsHeight(), this.trajectory.chromeHeight(), currentFontScale());
    this.topbar.setCrumbs(this.lineage());
    this.renderTabs();
  }

  /** The episodes the trajectory pane draws, in the tree's own order. */
  private trajectoryEpisodes(roots: ReturnType<typeof buildTree>): TrajectoryEpisode[] {
    return flatten(roots).map(({ node, depth }) => {
      const s = node.summary;
      return {
        id: s.id,
        name: s.name,
        identity: s.identity,
        depth,
        startTime: s.startTime,
        endTime: s.endTime,
        lastSeq: s.lastSeq,
        outcome: s.outcome,
        parentId: s.parentId,
        forkOrigin: s.forkOrigin,
        marks: s.marks,
        firings: s.firings,
        decisions: s.decisions,
      };
    });
  }

  /**
   * The episodes the causality figure draws. It reads the folded rows and
   * summary rather than the log, so no event is parsed twice and every
   * edge it draws comes from an obligation pair the fold already matched.
   */
  private causalityEpisodes(roots: ReturnType<typeof buildTree>): CausalityEpisode[] {
    return flatten(roots).map(({ node, depth }) => {
      const state = this.episodes.get(node.id);
      return readCausality(node.summary, state ? state.fold.rows : [], depth);
    });
  }

  /**
   * Scopes the conversation to one node of the causality figure. The pane
   * shows the node's own messages and those of every node below it, with a
   * header naming the scope and an escape back to the whole run.
   */
  private setScope(scope: ConversationScope | null): void {
    this.scope = scope;
    if (scope !== null && this.tab !== "conversation") this.tab = "conversation";
    this.renderMain();
  }

  private tabButton(tab: Tab, label: string): HTMLElement {
    return h("button", { class: "tab", type: "button", role: "tab", "data-tab": tab, onclick: () => this.setTab(tab) }, label);
  }

  /**
   * Feeds the two tabs that derive their own view of the selected episode.
   * Both gate on a digest of what they would draw, so this is called on
   * every redraw and costs nothing when nothing changed.
   */
  private renderTabs(): void {
    const id = this.selected ?? "";
    // The workflow tab appears once the episode's `episode/start` has been
    // read and declares a graph, which in live mode is after the first
    // selection is made.
    const declared = this.declaresWorkflow(id);
    const button = this.tabsBar.querySelector<HTMLElement>('.tab[data-tab="workflow"]');
    if (button) button.hidden = !declared;
    const state = this.episodes.get(id);
    if (!state) {
      this.workflow.update(null, null);
      this.statistics.update([], new Map(), this.rootScopes());
      return;
    }
    const summary = state.fold.summary;
    this.workflow.update(readWorkflow(summary.program, state.fold.events), id);
    const names = new Map<string, string>();
    for (const s of this.summaries()) names.set(s.id, s.name === s.id ? s.id : `${s.name} ${s.id}`);
    this.statistics.update(this.statisticsScope(id), names, this.rootScopes());
  }

  /** One episode and its descendants, each with its depth below it. */
  private scopeOf(node: TreeNode, depth = 0): StatisticsEpisode[] {
    const state = this.episodes.get(node.id);
    const s = node.summary;
    const own: StatisticsEpisode[] = state
      ? [
          {
            id: s.id,
            name: s.name,
            events: state.fold.events,
            startTime: s.startTime,
            endTime: s.endTime,
            program: s.program,
            depth,
            outcome: s.outcome,
          },
        ]
      : [];
    return [...own, ...node.children.flatMap((child) => this.scopeOf(child, depth + 1))];
  }

  /** The selected episode followed by its descendants, each with its depth. */
  private statisticsScope(id: string): StatisticsEpisode[] {
    const found = flatten(buildTree(this.summaries(), this.orderIds)).find((f) => f.node.id === id);
    return found ? this.scopeOf(found.node) : [];
  }

  /** One scope per root, which the run comparison reports one row each. */
  private rootScopes(): StatisticsEpisode[][] {
    return buildTree(this.summaries(), this.orderIds).map((root) => this.scopeOf(root));
  }

  private renderMain(): void {
    for (const b of this.tabsBar.querySelectorAll<HTMLElement>(".tab")) {
      const active = b.dataset.tab === this.tab;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", active ? "true" : "false");
    }
    this.renderTabs();
    const selected = this.selected ? this.episodes.get(this.selected) : undefined;
    let next: HTMLElement;
    if (this.tab === "diff") {
      next = this.diffElement();
    } else if (!selected) {
      next = h("div", { class: "empty" }, this.live ? "waiting for episodes" : "no episodes in this log");
    } else if (this.tab === "workflow") {
      next = this.workflow.el;
    } else if (this.tab === "statistics") {
      next = this.statistics.el;
    } else if (this.tab === "conversation" && this.scope !== null) {
      next = renderScope(
        this.scope,
        {
          rows: (id) => this.episodes.get(id)?.fold.rows ?? [],
          name: (id) => {
            const s = this.episodes.get(id)?.fold.summary;
            return s ? (s.name === s.id ? s.id : `${s.name} ${s.id}`) : id;
          },
        },
        { select: (id: string) => this.select(id) },
        () => this.trajectory.setScope(null),
      );
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
    // A tab has no width until it is mounted, so the two figures that fit
    // themselves to the pane draw once they are in the page.
    if (next === this.workflow.el) this.workflow.resized();
    if (next === this.statistics.el) this.statistics.resized();
    const remembered = this.scrollMemory.get(next);
    // A dialogue opens at its end, because the last row is the newest; a
    // figure opens at its top, because the first figure is the first to read.
    // A dialogue opens at its end and a figure at its top; a scoped
    // dialogue opens at its top too, because its first section is the node
    // that was selected and the reason the scope exists.
    const foot = next !== this.workflow.el && next !== this.statistics.el && !next.classList.contains("scoped");
    next.scrollTop = remembered === undefined ? (foot ? next.scrollHeight : 0) : remembered;
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
      default: {
        const tab = TAB_KEYS[Number(e.key) - 1];
        if (tab !== undefined && /^[1-9]$/.test(e.key)) this.setTab(tab);
        break;
      }
    }
  }
}
