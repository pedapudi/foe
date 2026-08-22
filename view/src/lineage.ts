// Episode lineage derived from each log's `episode/start`, and the length of
// the log prefix two episodes share through forking.

import type { Summary } from "./fold.js";

export interface TreeNode {
  id: string;
  summary: Summary;
  /** True when the edge to the parent is a fork rather than a spawn. */
  fork: boolean;
  children: TreeNode[];
}

/**
 * The identity hash shortened to the first eight characters of its digest,
 * which is enough to tell two programs apart by eye. The whole value goes
 * in the hovercard beside it.
 */
export function shortIdentity(identity: string): string {
  const digest = identity.startsWith("sha256:") ? identity.slice(7) : identity;
  return digest.length <= 8 ? digest : `${digest.slice(0, 8)}…`;
}

/**
 * Roots of one program stand together. Two episodes of one program carry
 * one `identity`, the hash over everything that shapes what the model
 * sees, so grouping by it puts comparable runs side by side and leaves
 * unrelated episodes apart. A root whose `episode/start` has not been read
 * has no identity and stands alone until it has.
 */
function byProgram(roots: TreeNode[]): TreeNode[] {
  const groups = new Map<string, TreeNode[]>();
  for (const root of roots) {
    const identity = root.summary.identity;
    const group = identity === "" ? undefined : groups.get(identity);
    if (group) group.push(root);
    else groups.set(identity === "" ? `episode:${root.id}` : identity, [root]);
  }
  return [...groups.values()].flat();
}

/**
 * Builds the forest. A spawned child hangs under `parent_id`; an episode
 * with a fork origin and no parent hangs under the origin. An episode whose
 * parent is absent from the set is a root, and roots of one program stand
 * together.
 */
export function buildTree(summaries: Summary[], order: string[] = []): TreeNode[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  const nodes = new Map<string, TreeNode>();
  for (const s of summaries) {
    nodes.set(s.id, { id: s.id, summary: s, fork: s.forkOrigin !== null, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of nodes.values()) {
    const s = node.summary;
    const parentId = s.parentId ?? s.forkOrigin?.episodeId ?? null;
    const parent = parentId !== null ? nodes.get(parentId) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  const byOrder = (a: TreeNode, b: TreeNode) => {
    const ra = rank.get(a.id);
    const rb = rank.get(b.id);
    if (ra !== undefined && rb !== undefined) return ra - rb;
    if (ra !== undefined) return -1;
    if (rb !== undefined) return 1;
    return a.summary.startTime - b.summary.startTime || a.id.localeCompare(b.id);
  };
  const sortAll = (list: TreeNode[]) => {
    list.sort(byOrder);
    for (const n of list) sortAll(n.children);
  };
  sortAll(roots);
  return byProgram(roots);
}

/** Consecutive rows that are runs of one program, in `programRuns`. */
export interface ProgramRun {
  identity: string;
  /** The program name, which every root of the group carries. */
  name: string;
  /** Roots in the group, which is how many runs of the program there are. */
  runs: number;
  /** First and last row of the group in the order given. */
  first: number;
  last: number;
}

/**
 * The programs that more than one root of `rows` ran, as spans of
 * consecutive rows. Roots of one program stand together and each root's
 * descendants follow it, so a program's rows are one span. A program with
 * a single root is left out: a bracket around one row groups nothing.
 * `rows` is the flattened tree, root rows at depth 0.
 */
export function programRuns(rows: { identity: string; name: string; depth: number }[]): ProgramRun[] {
  const spans: ProgramRun[] = [];
  rows.forEach((row, index) => {
    let span = spans[spans.length - 1];
    if (row.depth === 0 && !(span && span.identity === row.identity)) {
      spans.push({ identity: row.identity, name: row.name, runs: 1, first: index, last: index });
      return;
    }
    if (!span) return;
    if (row.depth === 0) span.runs += 1;
    span.last = index;
  });
  return spans.filter((span) => span.runs > 1 && span.identity !== "");
}

/** Depth-first order with depth, for keyboard navigation and rendering. */
export function flatten(roots: TreeNode[]): { node: TreeNode; depth: number }[] {
  const out: { node: TreeNode; depth: number }[] = [];
  const walk = (node: TreeNode, depth: number) => {
    out.push({ node, depth });
    for (const c of node.children) walk(c, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  return out;
}

/**
 * For each ancestor reachable through fork origins, the number of leading
 * events of `id`'s log that equal that ancestor's leading events. The
 * episode itself maps to Infinity.
 */
function forkAncestry(id: string, summaries: Map<string, Summary>): Map<string, number> {
  const out = new Map<string, number>();
  let current = id;
  let length = Infinity;
  while (!out.has(current)) {
    out.set(current, length);
    const s = summaries.get(current);
    if (!s || !s.forkOrigin) break;
    length = Math.min(length, s.forkOrigin.seq);
    current = s.forkOrigin.episodeId;
  }
  return out;
}

/**
 * Number of leading events (seq 0 to N-1) the two logs share through a
 * common fork origin, or 0 when they share none. Seq 0 differs in its
 * `id` and `fork_origin` fields and counts as shared because seeding
 * copies every other field.
 */
export function sharedPrefix(a: string, b: string, summaries: Map<string, Summary>): number {
  if (a === b) return 0;
  const fa = forkAncestry(a, summaries);
  const fb = forkAncestry(b, summaries);
  let best = 0;
  for (const [id, la] of fa) {
    const lb = fb.get(id);
    if (lb === undefined) continue;
    const shared = Math.min(la, lb);
    if (Number.isFinite(shared) && shared > best) best = shared;
  }
  return best;
}

/**
 * The tokens an episode spent: the input and output its answers reported,
 * summed over the episode's own log. Cache reads are inside the input
 * figure and are not added again.
 */
export function spentTokens(summary: Summary): number {
  return summary.usage.input + summary.usage.output;
}

/**
 * How much of a sibling group's spending each episode did, as a fraction of
 * the largest spender among them, itself included. An episode with no
 * sibling that spent anything has no share.
 *
 * Tokens are the quantity, because tokens are the one spendable thing a
 * tree divides without overlap: a parent and its children draw on one
 * budget pool, and every episode's spending is its own. Wall clock does
 * not divide that way, since siblings that ran at the same time each hold
 * the whole interval and their durations sum past their parent's.
 */
export function siblingShares(roots: TreeNode[]): Map<string, number> {
  const shares = new Map<string, number>();
  const group = (siblings: TreeNode[]) => {
    let largest = 0;
    for (const node of siblings) largest = Math.max(largest, spentTokens(node.summary));
    for (const node of siblings) {
      if (largest > 0) shares.set(node.id, spentTokens(node.summary) / largest);
      group(node.children);
    }
  };
  group(roots);
  return shares;
}
