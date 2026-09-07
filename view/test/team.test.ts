import assert from "node:assert/strict";
import { test } from "node:test";
import { readTaskBoard } from "../src/team.js";
import type { TeamEpisode } from "../src/team.js";

const episode: TeamEpisode = {
  id: "ep_lead",
  name: "lead",
  task: "Deliver the change.",
  depth: 0,
  outcome: null,
  events: [
    { seq: 0, time: 1, type: "episode/start", data: { id: "ep_lead" } },
    {
      seq: 1,
      time: 2,
      type: "team/task",
      data: {
        task_id: "task_01",
        revision: 0,
        name: "review",
        description: "Review the change.",
        status: "queued",
        owner: null,
        blocked_by: [],
        scope: ["src"],
      },
    },
    {
      seq: 2,
      time: 3,
      type: "team/task",
      data: {
        task_id: "task_01",
        revision: 1,
        name: "review",
        description: "Review the change.",
        status: "running",
        owner: "ep_review",
        blocked_by: [],
        scope: ["src"],
      },
    },
  ],
};

test("a board derives the root task and folds task revisions", () => {
  const board = readTaskBoard(episode);
  assert.equal(board.leadId, "ep_lead");
  assert.deepEqual(board.tasks.map((task) => task.id), ["task_root", "task_01"]);
  assert.equal(board.tasks[0]!.status, "running");
  assert.equal(board.tasks[0]!.owner, "ep_lead");
  assert.equal(board.tasks[1]!.revision, 1);
  assert.equal(board.tasks[1]!.owner, "ep_review");
  assert.deepEqual(board.tasks[1]!.transitions.map((transition) => transition.status), ["queued", "running"]);
});

test("the root task follows the episode outcome without a task event", () => {
  const ended: TeamEpisode = {
    ...episode,
    outcome: { kind: "completed", value: {} },
    events: [...episode.events, { seq: 3, time: 4, type: "episode/end", data: { outcome: { kind: "completed" } } }],
  };
  const root = readTaskBoard(ended).tasks[0]!;
  assert.equal(root.status, "completed");
  assert.deepEqual(root.transitions.map((transition) => transition.status), ["running", "completed"]);
  assert.equal(ended.events.some((event) => event.type === "team/task" && event.data.task_id === "task_root"), false);
});
