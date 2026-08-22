// The workflow tab: the graph an episode declares, with the run drawn over
// it. Line art in the register docs/design-language.md sets out.
//
// Both halves are on the page at once. Structure is faint: every declared
// node stands whether or not it fired, every declared edge is drawn whether
// or not a value crossed it, and every declared label is drawn whether or
// not a firing chose it. The run is the weight over that structure: a
// traversed edge is solid, a node takes its outcome direction from its last
// firing, and the labels the model chose carry the figure's one accent,
// because the choice inside a bounded graph is what the figure argues.

import { clear, fmtDuration, h } from "../dom.js";
import { TASK_SOURCE, layoutWorkflow } from "../workflow.js";
import type { PlacedNode, Workflow, WorkflowLayout, WorkflowNode } from "../workflow.js";
import { Hovercard } from "./hovercard.js";
import { figureSvg, svg } from "./svg.js";

function text(cls: string, x: number, y: number, body: string, anchor?: string): SVGTextElement {
  const el = svg("text", { class: cls, x, y, "text-anchor": anchor });
  el.textContent = body;
  return el;
}

export interface WorkflowHandlers {
  /** Selects the child episode a model node's firing ran. */
  select(id: string): void;
}

/** One line naming what a node is, under its name in the box. */
function kindLine(node: WorkflowNode): string {
  switch (node.kind) {
    case "tool":
      return `tool ${node.detail}`;
    case "model":
      return node.detail ? `model ${node.detail}` : "model";
    case "workflow":
      return "nested workflow";
    default:
      return "node";
  }
}

export class WorkflowView {
  readonly el: HTMLElement;
  private readonly figure = h("div", { class: "wf-figure" });
  private readonly caption = h("div", { class: "fig-caption" });
  private readonly card: Hovercard;
  private workflow: Workflow | null = null;
  private selected: string | null = null;
  private digest = "";

  constructor(private readonly handlers: WorkflowHandlers) {
    this.el = h("div", { class: "wf" });
    this.card = new Hovercard(this.el);
    this.el.append(
      h(
        "div",
        { class: "fig-head" },
        h("h3", null, "declared graph"),
        h("span", { class: "spacer" }),
        legend(),
      ),
      this.figure,
      this.caption,
      this.card.el,
    );
    this.el.addEventListener("scroll", () => this.card.hide());
  }

  update(workflow: Workflow | null, selected: string | null): void {
    this.workflow = workflow;
    this.selected = selected;
    this.draw();
  }

  /** Redraws when the tab's width changed, without changing what it shows. */
  resized(): void {
    this.draw();
  }

  private draw(): void {
    const width = this.figure.clientWidth;
    const workflow = this.workflow;
    if (!workflow) {
      this.digest = "";
      clear(this.figure);
      this.figure.appendChild(h("div", { class: "empty sub" }, "this episode declares no workflow"));
      this.caption.textContent = "";
      return;
    }
    const digest = [
      width,
      this.selected,
      workflow.chosen.join(","),
      workflow.recoveries.length,
      workflow.nodes
        .map((n) => `${n.name}:${n.direction}:${n.running ? 1 : 0}:${n.firings.length}`)
        .join("|"),
      workflow.edges.map((e) => (e.traversed ? 1 : 0)).join(""),
    ].join("~");
    if (digest === this.digest) return;
    // A tab that is not mounted has no width; the digest is kept unset so
    // that mounting it draws.
    if (width === 0) return;
    this.digest = digest;
    const layout = layoutWorkflow(workflow, width);
    clear(this.figure);
    this.card.hide();
    this.figure.appendChild(this.build(workflow, layout));
    const fired = workflow.nodes.filter((n) => n.firings.length > 0).length;
    this.caption.textContent =
      `${workflow.nodes.length} declared node${workflow.nodes.length === 1 ? "" : "s"}, ${fired} of which fired. ` +
      "A solid edge carried a value and a faint one was declared and never traversed; " +
      "the accented label is the one a firing chose.";
  }

  private build(workflow: Workflow, layout: WorkflowLayout): SVGSVGElement {
    const figure = figureSvg("wf-graph", layout.width, layout.height, "declared workflow graph");
    const byName = new Map(workflow.nodes.map((n) => [n.name, n]));
    const placedByName = new Map(layout.nodes.map((n) => [n.name, n]));

    // Edges first, so that the boxes paint over them.
    const edges = svg("g", { class: "wf-edges" });
    for (const edge of layout.edges) {
      const chosen = edge.label !== null && workflow.chosen.includes(`${edge.from}/${edge.label}`);
      const classes = ["wf-edge"];
      if (!edge.traversed) classes.push("faint");
      if (chosen) classes.push("chosen");
      const path = svg("path", { class: classes.join(" "), d: edgePath(edge) });
      const label = edge.label === null ? "an ordinary edge" : `the ${edge.label} branch`;
      this.card.attach(
        path,
        () => `${edge.from} → ${edge.to}`,
        () => label,
        () => (edge.traversed ? "a value crossed this edge" : "declared; no value crossed it"),
      );
      edges.appendChild(path);
    }
    for (const stub of layout.stubs) {
      const chosen = workflow.chosen.includes(`${stub.node}/${stub.label}`);
      const group = svg("g", { class: `wf-stub${chosen ? " chosen" : ""}` });
      group.appendChild(
        svg("path", { class: "wf-edge faint", d: `M ${stub.from_.x} ${stub.from_.y} H ${stub.to_.x}` }),
      );
      group.appendChild(svg("rect", { class: "wf-end", x: stub.to_.x, y: stub.to_.y - 3.5, width: 7, height: 7 }));
      this.card.attach(
        group,
        () => `${stub.node} → ${stub.label}`,
        () => "a label with no successor",
        () => (chosen ? "chosen: the workflow ended along this path" : "declared; never chosen"),
      );
      edges.appendChild(group);
    }
    figure.appendChild(edges);

    // Branch labels sit on the anchors their edges leave from, so a choice
    // point reads as one list of the labels it declares.
    const labels = svg("g", { class: "wf-labels" });
    for (const node of layout.nodes) {
      for (const anchor of node.anchors) {
        const chosen = workflow.chosen.includes(`${node.name}/${anchor.label}`);
        const group = svg("g", { class: `wf-label${chosen ? " chosen" : ""}` });
        group.appendChild(
          svg("path", {
            class: "leader",
            d: `M ${anchor.point.x} ${anchor.point.y} L ${anchor.labelPoint.x - 2} ${anchor.labelPoint.y}`,
          }),
        );
        group.appendChild(text("text", anchor.labelPoint.x, anchor.labelPoint.y + 3.5, anchor.label));
        const declared = byName.get(node.name)?.branches.find((b) => b.label === anchor.label);
        const successors = declared && declared.successors.length > 0 ? declared.successors.join(", ") : "no successor";
        this.card.attach(
          group,
          () => `${node.name}: ${anchor.label}`,
          () => `leads to ${successors}`,
          () => (chosen ? "a firing chose this label" : "declared; no firing chose it"),
        );
        labels.appendChild(group);
      }
    }
    figure.appendChild(labels);

    for (const placed of layout.nodes) {
      const node = byName.get(placed.name);
      figure.appendChild(node ? this.nodeElement(placed, node) : this.sourceElement(placed));
    }

    // A recovery marks where the failed node's inputs arrive, with the
    // action it took and the cause it took it for.
    const marks = svg("g", { class: "wf-recoveries" });
    for (const recovery of layout.recoveries) {
      const placed = placedByName.get(recovery.node);
      const group = svg("g", { class: "wf-recovery" });
      group.appendChild(
        svg("path", {
          class: "glyph",
          d: `M ${recovery.at.x - 5} ${recovery.at.y} l 5 -5 l 5 5 l -5 5 z`,
        }),
      );
      if (placed) {
        group.appendChild(
          text(
            "wf-recovery-label",
            placed.x + placed.width / 2,
            placed.y + placed.height + 12,
            `${recovery.action} · ${recovery.cause}`,
            "middle",
          ),
        );
      }
      if (recovery.to_ !== null) {
        group.appendChild(
          svg("path", {
            class: "wf-recovery-edge",
            d: `M ${recovery.at.x} ${recovery.at.y} C ${recovery.at.x - 40} ${recovery.at.y + 26}, ${
              recovery.to_.x - 40
            } ${recovery.to_.y + 26}, ${recovery.to_.x} ${recovery.to_.y}`,
          }),
        );
      }
      this.card.attach(
        group,
        () => `recovery ${recovery.intervention}`,
        () => `${recovery.node} firing ${recovery.fire} failed: ${recovery.cause}`,
        () =>
          `${recovery.action}${recovery.target ? ` ${recovery.target}` : ""}${
            recovery.note ? ` · ${recovery.note}` : ""
          }`,
      );
      marks.appendChild(group);
    }
    figure.appendChild(marks);
    return figure;
  }

  /** The built-in `task` source, which produces the invocation task once. */
  private sourceElement(placed: PlacedNode): SVGGElement {
    const group = svg("g", { class: "wf-node source" });
    group.appendChild(
      svg("rect", { class: "box", x: placed.x, y: placed.y, width: placed.width, height: placed.height, rx: 4 }),
    );
    group.appendChild(text("name", placed.x + 10, placed.y + 20, TASK_SOURCE));
    group.appendChild(text("kind", placed.x + 10, placed.y + 34, "built-in source"));
    this.card.attach(
      group,
      () => TASK_SOURCE,
      () => "the built-in source of the invocation task",
      () => "produced once, before the first firing",
    );
    return group;
  }

  private nodeElement(placed: PlacedNode, node: WorkflowNode): SVGGElement {
    const classes = ["wf-node"];
    if (node.direction) classes.push(node.direction);
    if (node.running) classes.push("running");
    if (node.firings.length === 0) classes.push("unfired");
    if (node.terminal) classes.push("terminal");
    const group = svg("g", { class: classes.join(" "), "data-node": node.name });
    group.appendChild(
      svg("rect", { class: "box", x: placed.x, y: placed.y, width: placed.width, height: placed.height, rx: 4 }),
    );
    group.appendChild(text("name", placed.x + 10, placed.y + 20, node.name));
    group.appendChild(text("kind", placed.x + 10, placed.y + 34, kindLine(node)));
    if (node.firings.length > 1) {
      group.appendChild(
        text("fires", placed.x + placed.width - 8, placed.y + 15, `×${node.firings.length}`, "end"),
      );
    }
    const bound = node.maxFires === null ? "" : ` of at most ${node.maxFires}`;
    this.card.attach(
      group,
      () => node.name,
      () => `${kindLine(node)}${node.terminal ? " · terminal" : ""}`,
      () =>
        node.firings.length === 0
          ? "declared; never fired"
          : `${node.firings.length} firing${node.firings.length === 1 ? "" : "s"}${bound}`,
    );

    // One mark per firing along the bottom edge. A model node's firing is a
    // child episode, and clicking its mark selects that episode, exactly as
    // clicking a mark in the trajectory does.
    const firings = node.firings;
    firings.forEach((firing, index) => {
      const x = placed.x + (placed.width * (index + 1)) / (firings.length + 1);
      const y = placed.y + placed.height;
      const linked = firing.childId !== null;
      const mark = svg("g", {
        class: `wf-firing${linked ? " linked" : ""}${
          firing.childId !== null && firing.childId === this.selected ? " selected" : ""
        }`,
      });
      mark.appendChild(svg("circle", { class: "glyph", cx: x, cy: y, r: 3.4 }));
      if (linked) mark.addEventListener("click", () => this.handlers.select(firing.childId!));
      this.card.attach(
        mark,
        () => `${node.name} firing ${firing.fire}`,
        () =>
          firing.durationMs === null
            ? "still running"
            : `${fmtDuration(firing.durationMs)}${firing.label ? ` · chose ${firing.label}` : ""}`,
        () => firing.error || (linked ? `child episode ${firing.childId}` : "ended with a value"),
      );
      group.appendChild(mark);
    });
    return group;
  }
}

/** The path of one edge: a curve forward, and a route under the rows back. */
function edgePath(edge: WorkflowLayout["edges"][number]): string {
  const { from_: a, to_: b } = edge;
  if (!edge.back) {
    const mid = (a.x + b.x) / 2;
    return `M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`;
  }
  const dip = edge.dip;
  return `M ${a.x} ${a.y} C ${a.x + 26} ${a.y}, ${a.x + 26} ${dip}, ${a.x} ${dip} H ${b.x - 26} C ${
    b.x - 40
  } ${dip}, ${b.x - 26} ${b.y}, ${b.x} ${b.y}`;
}

function legend(): HTMLElement {
  const swatch = (cls: string, label: string) =>
    h("span", { class: "legend-item" }, h("span", { class: `legend-mark ${cls}` }), label);
  return h(
    "div",
    { class: "fig-legend" },
    swatch("solid", "carried a value"),
    swatch("faint", "never traversed"),
    swatch("chosen", "the label chosen"),
  );
}
