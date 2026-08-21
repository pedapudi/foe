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
 * Builds the forest. A spawned child hangs under `parent_id`; an episode
 * with a fork origin and no parent hangs under the origin. An episode whose
 * parent is absent from the set is a root.
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
  return roots;
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
