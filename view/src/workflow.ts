// The declared graph of a workflow episode and what the run did inside it.
// The module holds no element and reads no document: `readWorkflow` folds a
// log into a graph and `layoutWorkflow` places that graph, so both are
// tested directly and render/workflow.ts draws what they return.
//
// Both halves are drawn, because a workflow's argument is that the declared
// graph bounds what the model may do while the model chooses freely inside
// it. A drawing of the firings alone would lose the bound: a node that
// never fired, a label the model did not choose, and an edge no value
// crossed are all part of what the run means.
//
// docs/workflow.md specifies the graph and docs/log-format.md the four
// `workflow/*` events this reads.

import { arr, num, obj, str } from "./types.js";
import type { LogEvent } from "./types.js";

/** The built-in source that carries the invocation task into the graph. */
export const TASK_SOURCE = "task";

export type NodeKind = "tool" | "model" | "workflow" | "unknown";

/** One label of a choice point, with the successors it fires. */
export interface Branch {
  label: string;
  successors: string[];
}

/** One firing of one node, from `workflow/node-start` to `workflow/node-end`. */
export interface Firing {
  fire: number;
  startSeq: number;
  startTime: number;
  /** Absent while the firing runs. */
  endSeq: number | null;
  endTime: number | null;
  durationMs: number | null;
  /** The error the firing ended with, empty when it ended cleanly. */
  error: string;
  /** The child episode of a model node's firing. */
  childId: string | null;
  /** Seq of each event that produced a value this firing received. */
  inputs: number[];
  /** The label this firing chose, absent when the node declares none. */
  label: string | null;
}

/** One applied recovery decision, from `workflow/recovery`. */
export interface Recovery {
  seq: number;
  /** The node that failed. */
  node: string;
  fire: number;
  cause: string;
  /** One of retry, amend, skip, and abort. */
  action: string;
  /** The node a retry or an amend re-fires. */
  target: string | null;
  note: string;
  intervention: number;
}

export interface WorkflowNode {
  name: string;
  kind: NodeKind;
  /** The tool a tool node calls or the program a model node runs. */
  detail: string;
  /** The node-level verifier the declaration names, empty for none. */
  verify: string;
  branches: Branch[];
  maxFires: number | null;
  terminal: boolean;
  firings: Firing[];
  /**
   * Colour direction, earned by the last firing's end and never by
   * identity: `bad` for an error, `good` for a clean end, and neutral for a
   * node that is still running or never fired.
   */
  direction: "good" | "bad" | "";
  running: boolean;
}

export interface Edge {
  /** Source node name, or `task` for the built-in source. */
  from: string;
  to: string;
  /** The choice-point label that carries this edge, null for a data edge. */
  label: string | null;
  /** True when some firing of the target received a value across this edge. */
  traversed: boolean;
}

export interface Workflow {
  /** Every declared node, in name order. */
  nodes: WorkflowNode[];
  edges: Edge[];
  recoveries: Recovery[];
  /** True when at least one node lists the built-in `task` source. */
  hasTask: boolean;
  /** Labels some firing chose, each written `node/label`. */
  chosen: string[];
}

/**
 * The workflow an episode declares, taken from `episode/start`'s `program`,
 * which is the resolved configuration. Absent for an episode that runs the
 * free loop.
 */
export function declaredWorkflow(program: Record<string, unknown>): Record<string, unknown> | null {
  const workflow = obj(program.workflow);
  return Object.keys(obj(workflow.nodes)).length > 0 ? workflow : null;
}

function kindOf(node: Record<string, unknown>): NodeKind {
  if (typeof node.tool === "string") return "tool";
  if (node.model !== undefined && node.model !== null) return "model";
  if (node.workflow !== undefined && node.workflow !== null) return "workflow";
  return "unknown";
}

function detailOf(node: Record<string, unknown>, kind: NodeKind): string {
  if (kind === "tool") return str(node.tool);
  if (kind === "model") return str(obj(node.model).name);
  return "";
}

/**
 * Folds a workflow episode's log into its declared graph and the run that
 * went through it. `program` is `episode/start`'s `program` and `events` is
 * the episode's own log; a log with no `workflow/*` event yields the
 * declaration with no firing, which is what a run that has not started yet
 * looks like.
 */
export function readWorkflow(program: Record<string, unknown>, events: LogEvent[]): Workflow | null {
  const declared = declaredWorkflow(program);
  if (declared === null) return null;
  const declaredNodes = obj(declared.nodes);
  const names = Object.keys(declaredNodes).sort();

  const nodes = new Map<string, WorkflowNode>();
  const edges: Edge[] = [];
  const edgeKey = new Map<string, Edge>();
  const addEdge = (from: string, to: string, label: string | null) => {
    // A node name may hold any character, so the parts are joined on one
    // that a JSON key never carries.
    const key = `${from}\u0000${to}\u0000${label ?? ""}`;
    if (edgeKey.has(key)) return;
    const edge: Edge = { from, to, label, traversed: false };
    edgeKey.set(key, edge);
    edges.push(edge);
  };

  let hasTask = false;
  for (const name of names) {
    const raw = obj(declaredNodes[name]);
    const kind = kindOf(raw);
    const branches: Branch[] = Object.keys(obj(raw.branches))
      .sort()
      .map((label) => ({ label, successors: arr(obj(raw.branches)[label]).map((s) => str(s)) }));
    nodes.set(name, {
      name,
      kind,
      detail: detailOf(raw, kind),
      verify: str(raw.verify),
      branches,
      maxFires: typeof raw.max_fires === "number" ? raw.max_fires : null,
      terminal: raw.terminal === true,
      firings: [],
      direction: "",
      running: false,
    });
    for (const source of arr(raw.follows).map((s) => str(s))) {
      if (source === TASK_SOURCE) hasTask = true;
      addEdge(source, name, null);
    }
    // Retained logs are immutable compatibility inputs and may use the outgoing spelling.
    for (const target of arr(raw.followed_by).map((s) => str(s))) addEdge(name, target, null);
  }
  // A branch edge is declared from the other end and may name a node that
  // no `follows` list mentions, so it is added after every node is known.
  for (const node of nodes.values()) {
    for (const branch of node.branches) {
      for (const successor of branch.successors) addEdge(node.name, successor, branch.label);
    }
  }

  const byType = (seq: number): LogEvent | undefined => events.find((e) => e.seq === seq);
  const producer = new Map<number, string>();
  const recoveries: Recovery[] = [];
  const chosen = new Set<string>();
  const open = new Map<string, Firing>();

  for (const event of events) {
    const data = obj(event.data);
    const name = str(data.node);
    const node = nodes.get(name);
    switch (event.type) {
      case "workflow/node-start": {
        if (!node) break;
        const firing: Firing = {
          fire: num(data.fire, node.firings.length + 1),
          startSeq: event.seq,
          startTime: event.time,
          endSeq: null,
          endTime: null,
          durationMs: null,
          error: "",
          childId: typeof data.child_id === "string" ? data.child_id : null,
          inputs: arr(data.inputs).map((s) => num(s)),
          label: null,
        };
        node.firings.push(firing);
        open.set(`${name}#${firing.fire}`, firing);
        // Each input names the event that produced a value this firing
        // received, so an edge is traversed exactly when an input of the
        // target names an event of the source.
        for (const seq of firing.inputs) {
          const from = producer.get(seq) ?? (byType(seq)?.type === "inbox/item" ? TASK_SOURCE : null);
          if (from === null) continue;
          for (const edge of edges) {
            if (edge.from === from && edge.to === name) edge.traversed = true;
          }
        }
        break;
      }
      case "workflow/node-end": {
        if (!node) break;
        const fire = num(data.fire, node.firings.length);
        const firing = open.get(`${name}#${fire}`) ?? node.firings[node.firings.length - 1];
        producer.set(event.seq, name);
        if (!firing) break;
        firing.endSeq = event.seq;
        firing.endTime = event.time;
        firing.durationMs = typeof data.duration_ms === "number" ? data.duration_ms : null;
        firing.error = str(data.error);
        open.delete(`${name}#${fire}`);
        break;
      }
      case "workflow/branch": {
        if (!node) break;
        const label = str(data.label);
        const fire = num(data.fire, node.firings.length);
        const firing = node.firings.find((f) => f.fire === fire);
        if (firing) firing.label = label;
        chosen.add(`${name}/${label}`);
        for (const successor of arr(data.successors).map((s) => str(s))) {
          for (const edge of edges) {
            if (edge.from === name && edge.to === successor && edge.label === label) edge.traversed = true;
          }
        }
        break;
      }
      case "workflow/recovery": {
        producer.set(event.seq, name);
        recoveries.push({
          seq: event.seq,
          node: name,
          fire: num(data.fire),
          cause: str(data.cause, "?"),
          action: str(data.action, "?"),
          target: typeof data.target === "string" ? data.target : null,
          note: str(data.note),
          intervention: num(data.intervention),
        });
        break;
      }
      default:
        break;
    }
  }

  for (const node of nodes.values()) {
    const last = node.firings[node.firings.length - 1];
    node.running = last !== undefined && last.endSeq === null;
    if (last === undefined || node.running) node.direction = "";
    else node.direction = last.error === "" ? "good" : "bad";
  }

  return {
    nodes: names.map((name) => nodes.get(name)!),
    edges,
    recoveries,
    hasTask,
    chosen: [...chosen].sort(),
  };
}

// ---- layout ----------------------------------------------------------------

export const NODE_WIDTH = 152;
export const NODE_HEIGHT = 48;
/** Narrowest a node box goes before the figure keeps its width and scrolls. */
const NODE_WIDTH_MIN = 104;
const GAP_X = 58;
const GAP_X_MIN = 30;
const GAP_Y = 26;
const PAD = 12;
/** Length of the stub that stands for a label with no successor. */
export const STUB_LENGTH = 30;
/** How far below the last row a back edge is routed. */
const BACK_DIP = 18;
/** Leader from a node's edge to the text of one of its labels. */
const LEADER = 11;
/** Distance between two label lines, which exceeds their type size. */
const LABEL_STEP = 13;
/** Advance of one character of label text, at the size the labels set. */
const LABEL_CHAR = 6;

export interface Point {
  x: number;
  y: number;
}

/** One declared label of a choice point, placed beside its node. */
export interface Anchor {
  label: string;
  /** Where the label's edges leave the node's own edge. */
  point: Point;
  /**
   * Where the label's text sits. Labels fan out further apart than the
   * node's edge allows, so that a choice point with several labels reads as
   * a list; a leader runs from `point` to here.
   */
  labelPoint: Point;
}

export interface PlacedNode {
  name: string;
  rank: number;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Every declared label of this node, in label order. */
  anchors: Anchor[];
}

export interface PlacedEdge extends Edge {
  from_: Point;
  to_: Point;
  /**
   * True when the edge runs back to a node at or before its source's rank,
   * which happens on a cycle; such an edge is routed under the rows.
   */
  back: boolean;
  /** The y a back edge is routed through. */
  dip: number;
}

/** A label whose successor list is empty: the workflow ends along that path. */
export interface PlacedStub {
  node: string;
  label: string;
  from_: Point;
  to_: Point;
}

export interface PlacedRecovery extends Recovery {
  /** The failed node's left edge, where its inputs arrive. */
  at: Point;
  /** The target's left edge when a retry or an amend names another node. */
  to_: Point | null;
}

export interface WorkflowLayout {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  stubs: PlacedStub[];
  recoveries: PlacedRecovery[];
  width: number;
  height: number;
  /** Node names by rank, which is the column order the figure draws. */
  ranks: string[][];
}

/**
 * Depth-first walk from the sources that names every edge closing a cycle.
 * Sources, and each node's successors, are visited in name order, so the
 * same graph always yields the same set.
 */
function backEdges(names: string[], edges: Edge[]): Set<Edge> {
  const out = new Map<string, Edge[]>();
  const indegree = new Map<string, number>(names.map((n) => [n, 0]));
  for (const edge of edges) {
    if (!indegree.has(edge.from) || !indegree.has(edge.to)) continue;
    const list = out.get(edge.from);
    if (list) list.push(edge);
    else out.set(edge.from, [edge]);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }
  for (const list of out.values()) list.sort((a, b) => a.to.localeCompare(b.to));
  const back = new Set<Edge>();
  const seen = new Set<string>();
  const stack = new Set<string>();
  const walk = (name: string) => {
    seen.add(name);
    stack.add(name);
    for (const edge of out.get(name) ?? []) {
      if (stack.has(edge.to)) back.add(edge);
      else if (!seen.has(edge.to)) walk(edge.to);
    }
    stack.delete(name);
  };
  for (const name of names) if ((indegree.get(name) ?? 0) === 0) walk(name);
  for (const name of names) if (!seen.has(name)) walk(name);
  return back;
}

/**
 * The rank of every node: the length of the longest path of forward edges
 * that reaches it. Edges that close a cycle are left out of the ranking and
 * drawn as returns, so a bounded cycle does not push its own rank. `names`
 * holds the declared nodes and never the `task` source, which imposes no
 * order and takes a column of its own.
 */
export function rankNodes(names: string[], edges: Edge[]): Map<string, number> {
  const back = backEdges(names, edges);
  const forward = edges.filter((e) => !back.has(e) && names.includes(e.from) && names.includes(e.to));
  const rank = new Map<string, number>(names.map((n) => [n, 0]));
  // The graph without its back edges is acyclic, so one pass per node
  // settles every rank; the bound also stops a graph this reader does not
  // understand from looping.
  for (let pass = 0; pass < names.length; pass++) {
    let moved = false;
    for (const edge of forward) {
      const wanted = (rank.get(edge.from) ?? 0) + 1;
      if (wanted > (rank.get(edge.to) ?? 0)) {
        rank.set(edge.to, wanted);
        moved = true;
      }
    }
    if (!moved) break;
  }
  return rank;
}

/**
 * Places the graph in columns by rank, each column in name order, so that
 * the same workflow always draws the same way. `width` is the room the pane
 * gives the figure: the columns and the boxes shrink to their minimums to
 * fit it, and a graph that still does not fit keeps its own width, which
 * the pane then scrolls.
 */
export function layoutWorkflow(workflow: Workflow, width: number): WorkflowLayout {
  const names = workflow.nodes.map((n) => n.name);
  const all = workflow.hasTask ? [TASK_SOURCE, ...names] : names;
  const edges = workflow.edges.filter((e) => all.includes(e.from) && all.includes(e.to));
  const rank = rankNodes(names, edges);
  const highest = Math.max(0, ...names.map((n) => rank.get(n) ?? 0));
  const ranks: string[][] = Array.from({ length: highest + 1 }, () => []);
  for (const name of names) ranks[rank.get(name) ?? 0]!.push(name);
  for (const column of ranks) column.sort();
  // The `task` source produces its value before any node fires and orders
  // nothing, so it stands in a column of its own ahead of every node.
  if (workflow.hasTask) ranks.unshift([TASK_SOURCE]);

  // The gap between two columns holds the branch labels of the left one, so
  // it is never narrower than the longest label needs.
  let labelRoom = 0;
  for (const node of workflow.nodes) {
    for (const branch of node.branches) {
      labelRoom = Math.max(labelRoom, LEADER + branch.label.length * LABEL_CHAR + 26);
    }
  }
  // A wide graph gives up its gaps first and its box width second; below
  // both minimums the figure keeps its natural width.
  const columns = ranks.length;
  const room = Math.max(0, width - 2 * PAD);
  const gapWanted = Math.max(GAP_X, labelRoom);
  const gapFloor = Math.max(GAP_X_MIN, labelRoom);
  const natural = columns * NODE_WIDTH + (columns - 1) * gapWanted;
  let nodeWidth = NODE_WIDTH;
  let gapX = gapWanted;
  if (columns > 0 && natural > room) {
    gapX = Math.max(gapFloor, (room - columns * NODE_WIDTH) / Math.max(1, columns - 1));
    if (columns * NODE_WIDTH + (columns - 1) * gapX > room) {
      nodeWidth = Math.max(NODE_WIDTH_MIN, (room - (columns - 1) * gapX) / columns);
    }
  }

  const declared = new Map(workflow.nodes.map((n) => [n.name, n]));
  // A choice point with many labels fans them past the top and bottom of
  // its box; the figure makes room for that overhang.
  let overhang = 0;
  for (const node of workflow.nodes) {
    const span = (node.branches.length - 1) * LABEL_STEP;
    overhang = Math.max(overhang, (span - NODE_HEIGHT) / 2 + 6);
  }
  overhang = Math.max(0, overhang);
  const top = PAD + overhang;
  const placed = new Map<string, PlacedNode>();
  const nodes: PlacedNode[] = [];
  ranks.forEach((column, index) => {
    column.forEach((name, row) => {
      const node = declared.get(name);
      const x = PAD + index * (nodeWidth + gapX);
      const y = top + row * (NODE_HEIGHT + GAP_Y);
      const labels = node ? node.branches.map((b) => b.label) : [];
      const middle = y + NODE_HEIGHT / 2;
      const anchors: Anchor[] = labels.map((label, i) => ({
        label,
        point: { x: x + nodeWidth, y: y + (NODE_HEIGHT * (i + 1)) / (labels.length + 1) },
        labelPoint: { x: x + nodeWidth + LEADER, y: middle + (i - (labels.length - 1) / 2) * LABEL_STEP },
      }));
      const spot: PlacedNode = { name, rank: index, x, y, width: nodeWidth, height: NODE_HEIGHT, anchors };
      placed.set(name, spot);
      nodes.push(spot);
    });
  });

  const rows = Math.max(1, ...ranks.map((c) => c.length));
  const body = rows * NODE_HEIGHT + (rows - 1) * GAP_Y;
  const height = top + body + overhang + PAD + BACK_DIP * 2;
  const figureWidth = PAD * 2 + columns * nodeWidth + Math.max(0, columns - 1) * gapX;
  const floor = top + body + overhang + BACK_DIP;

  // An edge under a label leaves from past the label's text, so that the
  // label reads as sitting on the edge it names.
  const leaves = (spot: PlacedNode, label: string | null): Point => {
    const anchor = label === null ? undefined : spot.anchors.find((a) => a.label === label);
    if (!anchor) return { x: spot.x + spot.width, y: spot.y + spot.height / 2 };
    return { x: anchor.labelPoint.x + anchor.label.length * LABEL_CHAR + 5, y: anchor.labelPoint.y };
  };
  const enters = (spot: PlacedNode): Point => ({ x: spot.x, y: spot.y + spot.height / 2 });

  const laid: PlacedEdge[] = [];
  for (const edge of edges) {
    const from = placed.get(edge.from);
    const to = placed.get(edge.to);
    if (!from || !to) continue;
    laid.push({ ...edge, from_: leaves(from, edge.label), to_: enters(to), back: to.rank <= from.rank, dip: floor });
  }

  const stubs: PlacedStub[] = [];
  for (const node of workflow.nodes) {
    const spot = placed.get(node.name);
    if (!spot) continue;
    for (const branch of node.branches) {
      if (branch.successors.length > 0) continue;
      const point = leaves(spot, branch.label);
      stubs.push({ node: node.name, label: branch.label, from_: point, to_: { x: point.x + STUB_LENGTH, y: point.y } });
    }
  }

  const recoveries: PlacedRecovery[] = [];
  for (const recovery of workflow.recoveries) {
    const spot = placed.get(recovery.node);
    if (!spot) continue;
    const target = recovery.target === null || recovery.target === recovery.node ? null : placed.get(recovery.target);
    recoveries.push({ ...recovery, at: enters(spot), to_: target ? enters(target) : null });
  }

  return { nodes, edges: laid, stubs, recoveries, width: figureWidth, height, ranks };
}
