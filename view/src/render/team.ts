// Task boards for the selected lead and each descendant that leads a team.
// Plain rows keep task state, dependencies, ownership, and scopes readable
// at the same time in both live and static views.

import { clear, h } from "../dom.js";
import { readTaskBoard } from "../team.js";
import type { TaskBoard, TeamEpisode, TeamTask } from "../team.js";

export interface TeamHandlers {
  select(id: string): void;
}

function direction(status: string): string {
  if (status === "completed") return "good";
  if (status === "blocked" || status === "exhausted") return "caution";
  if (status === "failed") return "bad";
  return "running";
}

function taskRow(task: TeamTask, handlers: TeamHandlers): HTMLElement {
  const owner = task.owner
    ? h("button", { class: "task-owner", type: "button", onclick: () => handlers.select(task.owner!) }, task.owner)
    : "unassigned";
  const dependencies = task.blockedBy.length ? `after ${task.blockedBy.join(", ")}` : "ready";
  const scope = task.scope.length ? task.scope.join(", ") : "unspecified";
  const history = task.transitions.map((transition) => transition.status).join(" → ");
  return h(
    "div",
    { class: "task-row" },
    h("div", { class: `task-status ${direction(task.status)}` }, task.status),
    h(
      "div",
      { class: "task-body" },
      h("div", { class: "task-name" }, task.name, h("span", { class: "task-id" }, task.id)),
      task.description ? h("div", { class: "task-description" }, task.description) : null,
      h(
        "dl",
        { class: "task-fields" },
        h("dt", null, "owner"),
        h("dd", null, owner),
        h("dt", null, "dependencies"),
        h("dd", null, dependencies),
        h("dt", null, "scope"),
        h("dd", null, scope),
        h("dt", null, "history"),
        h("dd", null, history),
      ),
    ),
  );
}

function boardElement(board: TaskBoard, selected: string | null, handlers: TeamHandlers): HTMLElement {
  const lead = h(
    "button",
    {
      class: `team-lead${board.leadId === selected ? " selected" : ""}`,
      type: "button",
      onclick: () => handlers.select(board.leadId),
    },
    board.leadName,
    h("span", { class: "team-id" }, board.leadId),
  );
  return h(
    "section",
    { class: "task-board", style: `--team-depth: ${board.depth}` },
    h("div", { class: "task-board-head" }, lead, h("span", { class: "task-count" }, `${board.tasks.length} tasks`)),
    h("div", { class: "task-list" }, board.tasks.map((task) => taskRow(task, handlers))),
  );
}

export class TeamView {
  readonly el = h("div", { class: "teams" });
  private digest = "";

  constructor(private readonly handlers: TeamHandlers) {}

  update(episodes: TeamEpisode[], selected: string | null): void {
    const boards = episodes.map(readTaskBoard).filter((board, index) => index === 0 || board.tasks.length > 1);
    const digest = JSON.stringify([selected, boards]);
    if (digest === this.digest) return;
    this.digest = digest;
    clear(this.el);
    const taskCount = boards.reduce((sum, board) => sum + board.tasks.length, 0);
    this.el.append(
      h(
        "div",
        { class: "fig-head" },
        h("h3", null, "task boards"),
        h("span", { class: "spacer" }),
        h("span", { class: "fig-total" }, `${boards.length} teams · ${taskCount} tasks`),
      ),
      h("div", { class: "task-boards" }, boards.map((board) => boardElement(board, selected, this.handlers))),
      h(
        "div",
        { class: "fig-caption" },
        "Each lead assigns ready tasks in creation order. A queued task starts when its dependencies complete and capacity is available.",
      ),
    );
  }
}
