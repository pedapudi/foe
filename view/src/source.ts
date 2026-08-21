// Where events come from. Static mode reads them from `window.__FOE__`;
// live mode fetches the episode tree and subscribes to each episode's
// server-sent-events stream. Both feed the same sink.

import type { ConnectionState } from "./chrome.js";
import { arr, num, obj, str } from "./types.js";
import type { LogEvent } from "./types.js";

export interface StaticConfig {
  mode: "static";
  episodes: Record<string, unknown[]>;
  tree?: unknown;
}

export interface LiveConfig {
  mode: "live";
  base?: string;
  token?: string;
}

export type Config = StaticConfig | LiveConfig;

export interface Sink {
  /** Episode ids in tree order. Called before any event and again when the tree grows. */
  order(ids: string[]): void;
  events(id: string, events: LogEvent[]): void;
  /** Connection state for the status pill, with a short word or phrase. */
  status(state: ConnectionState, detail: string): void;
}

/** Flattens `{ roots: [ { id, children } ] }` depth first; tolerates a bare list. */
export function treeOrder(tree: unknown): string[] {
  const out: string[] = [];
  const walk = (node: unknown) => {
    const n = obj(node);
    if (typeof n.id === "string") out.push(n.id);
    for (const c of arr(n.children)) walk(c);
  };
  const t = obj(tree);
  const roots = Array.isArray(tree) ? tree : arr(t.roots);
  for (const r of roots) walk(r);
  return out;
}

/** Parses one log line's object; a line that is not an event object is dropped. */
export function toEvent(raw: unknown): LogEvent | null {
  const o = obj(raw);
  if (typeof o.seq !== "number" || typeof o.type !== "string") return null;
  return { seq: o.seq, time: num(o.time), type: o.type, data: obj(o.data) };
}

export function start(config: Config, sink: Sink): void {
  if (config.mode === "live") startLive(config, sink);
  else startStatic(config, sink);
}

function startStatic(config: StaticConfig, sink: Sink): void {
  const episodes = obj(config.episodes) as Record<string, unknown[]>;
  const ids = Object.keys(episodes);
  const order = treeOrder(config.tree);
  const ordered = [...order.filter((id) => id in episodes), ...ids.filter((id) => !order.includes(id))];
  sink.order(ordered);
  for (const id of ordered) {
    const events = arr(episodes[id])
      .map(toEvent)
      .filter((e): e is LogEvent => e !== null);
    sink.events(id, events);
  }
  sink.status("file", "file");
}

const TREE_POLL_MS = 2000;

function startLive(config: LiveConfig, sink: Sink): void {
  const base = str(config.base).replace(/\/$/, "");
  const token = str(config.token);
  const headers: Record<string, string> = token ? { "X-Foe-Token": token } : {};
  const open = new Map<string, EventSource>();
  const ended = new Set<string>();
  let known: string[] = [];
  let listOk = false;

  const refreshStatus = () => {
    if (!listOk) {
      sink.status("unavailable", "episode list unavailable");
    } else if (open.size > 0) {
      sink.status("connected", `${open.size} stream${open.size === 1 ? "" : "s"}`);
    } else if (known.length > 0 && known.every((id) => ended.has(id))) {
      sink.status("ended", "ended");
    } else {
      sink.status("reconnecting", "connecting");
    }
  };

  const subscribe = (id: string) => {
    if (open.has(id) || ended.has(id)) return;
    const url = `${base}/events?episode=${encodeURIComponent(id)}${
      token ? `&token=${encodeURIComponent(token)}` : ""
    }`;
    const source = new EventSource(url);
    open.set(id, source);
    source.onmessage = (msg) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(String(msg.data));
      } catch {
        return;
      }
      const list = Array.isArray(parsed) ? parsed : [parsed];
      const events = list.map(toEvent).filter((e): e is LogEvent => e !== null);
      if (events.length === 0) return;
      sink.events(id, events);
      if (events.some((e) => e.type === "episode/end")) {
        ended.add(id);
        source.close();
        open.delete(id);
        refreshStatus();
      }
    };
    source.onerror = () => {
      sink.status("reconnecting", `reconnecting to ${id}`);
    };
    source.onopen = () => refreshStatus();
  };

  const poll = async () => {
    try {
      const res = await fetch(`${base}/episodes`, { headers });
      if (!res.ok) throw new Error(`${res.status}`);
      const order = treeOrder(await res.json());
      listOk = true;
      if (order.length !== known.length || order.some((id, i) => known[i] !== id)) {
        known = order;
        sink.order(order);
      }
      for (const id of order) subscribe(id);
      refreshStatus();
    } catch {
      listOk = false;
      refreshStatus();
    }
    // A parent can spawn a child at any time before its own end, so the
    // tree is polled until every known episode has ended.
    const allEnded = known.length > 0 && known.every((id) => ended.has(id));
    if (!allEnded) setTimeout(poll, TREE_POLL_MS);
  };
  void poll();
}
