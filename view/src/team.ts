// The durable task boards led by an episode and its descendants. The fold
// derives the lead's root task from the episode lifecycle and reads every
// delegated task from its revisioned `team/task` events.

import { arr, num, obj, str } from "./types.js";
import type { LogEvent, Outcome } from "./types.js";

export interface TeamEpisode {
  id: string;
  name: string;
  task: string;
  depth: number;
  events: LogEvent[];
  outcome: Outcome | null;
}

export interface TaskTransition {
  seq: number;
  time: number;
  status: string;
}

export interface TeamTask {
  id: string;
  revision: number;
  name: string;
  description: string;
  status: string;
  owner: string | null;
  blockedBy: string[];
  scope: string[];
  transitions: TaskTransition[];
}

export interface TaskBoard {
  leadId: string;
  leadName: string;
  depth: number;
  tasks: TeamTask[];
}

function outcomeStatus(outcome: Outcome | null): string {
  return outcome === null ? "running" : str(outcome.kind, "failed");
}

/** Fold one lead log into its current board and each task's full history. */
export function readTaskBoard(episode: TeamEpisode): TaskBoard {
  const start = episode.events.find((event) => event.type === "episode/start");
  const end = episode.events.find((event) => event.type === "episode/end");
  const tasks = new Map<string, TeamTask>();
  tasks.set("task_root", {
    id: "task_root",
    revision: end ? 1 : 0,
    name: episode.name,
    description: episode.task,
    status: outcomeStatus(episode.outcome),
    owner: episode.id,
    blockedBy: [],
    scope: [],
    transitions: [
      ...(start ? [{ seq: start.seq, time: start.time, status: "running" }] : []),
      ...(end ? [{ seq: end.seq, time: end.time, status: outcomeStatus(episode.outcome) }] : []),
    ],
  });

  for (const event of episode.events) {
    if (event.type !== "team/task") continue;
    const data = obj(event.data);
    const id = str(data.task_id);
    const revision = num(data.revision);
    if (!id) continue;
    const prior = tasks.get(id);
    if (prior && revision <= prior.revision) continue;
    const status = str(data.status, "queued");
    tasks.set(id, {
      id,
      revision,
      name: str(data.name, id),
      description: str(data.description),
      status,
      owner: typeof data.owner === "string" ? data.owner : null,
      blockedBy: arr(data.blocked_by).map((item) => str(item)).filter(Boolean),
      scope: arr(data.scope).map((item) => str(item)).filter(Boolean),
      transitions: [...(prior?.transitions ?? []), { seq: event.seq, time: event.time, status }],
    });
  }

  return { leadId: episode.id, leadName: episode.name, depth: episode.depth, tasks: [...tasks.values()] };
}
