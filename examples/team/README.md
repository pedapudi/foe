# Coordinated team demo

This example runs five agents across two nested teams. A lead changes a
command-line program. Review and test tasks run concurrently. Their completed
states unlock an integration task. The integration agent leads a nested team
that audits the command usage before the final check runs.

The response service is deterministic. The runner checks the resulting
workspace and every relevant coordination event. It requires no credential
and makes no endpoint request.

## Requirements

The example runs on Linux and uses `/usr/bin/python3` for its response
service, project check, and runner assertions.

## Run

From the repository root:

```sh
cargo build --release --bin foe
examples/team/run.sh
```

The runner accepts the binary path as its only argument. It defaults to
`target/release/foe`. Each run creates a directory named
`target/foe-team-demo.XXXXXX/`. The directory contains the materialized
configuration, the small project, and the complete episode tree.

The runner prints a viewer command after validation. Open the tasks tab to
see both boards, their task histories, dependencies, owners, and scopes.
The same projection is available while the run is live and from a static
viewer export.

## Coordination graph

The root board has one derived task and three added tasks:

```text
task_root  lead changes src/cli.py
    ├── task_01  review src/cli.py ───────┐
    ├── task_02  run tests/check.py ─────┤
    └── task_03  integrate ◄─────────────┘
                         │
                         └── nested task_01  audit usage and checks
```

The runtime derives `task_root` from the root episode. It writes no
`team/task` event for that task. The lead adds the other tasks with `spawn`.
Each call records a queued revision before scheduling the child.

The root permits two direct children at once. The scheduler assigns
`task_01` and `task_02` immediately. `task_03` names both as dependencies and
stays queued. A child settlement schedules ready work inside the runtime, so
the lead makes no polling model request.

The integration child inspects the parent-led board with `team`. It then
adds an audit task to the board it leads, waits for that task, runs the
complete check, and reports to the root. The nested board demonstrates that
every member can apply the same primitives at the next depth.

## Task assignment

Only a lead process writes its `team/task` events. The board order is
creation order. The scheduler scans that order and assigns each ready task
to one freshly created child episode. Agents do not race to claim work.

Each revision is a complete snapshot. A successful task has this history:

```text
queued → running → completed
```

The task carries an advisory write scope. Review names `src/cli.py`, testing
names `tests/check.py`, and integration names both. Scope communicates likely
overlap. Filesystem grants remain the enforced authority.

## Peer and parent messages

The reviewer asks the tester which checks cover the changed file. Both are
members of the root team. The reviewer calls `send` with the tester's roster
name. The lead records `team/message`, delivers an `inbox/item` with source
`peer`, and records `team/delivered` after the tester log receives it.

The tester answers through the same route. A member that needs the answer
calls `wait` for a peer inbox item. The wait blocks inside the runtime and
uses no model request.

The nested auditor calls `notify`. Its report becomes a child inbox item in
the integration log. The integration agent reports the combined result to
the root in the same way. Cross-team communication therefore follows the
team hierarchy through durable inbox items.

## Lifecycle and shutdown

A child enters its parent's roster as `provisioning` and becomes `active`
when its `episode/start` arrives. An abnormal process end changes the phase
to `failed`. The assigned task records every ordinary terminal outcome. The
folded team view attaches that task status to its member. There is no idle
worker pool. One task creates one child. The child process ends when its
episode reaches an outcome.

A bare `wait` returns when every added task on the caller's board has a
terminal status and no child reservation remains. The root waits for three
tasks. Integration waits for its nested audit. Since integration cannot
settle before its own wait returns, the root wait covers the complete
descendant tree.

## Runner assertions

The runner checks these invariants:

- The root log contains no `team/task` event for `task_root`.
- Every added task records `queued`, `running`, and `completed` in order.
- Review and testing overlap.
- Integration starts after both dependency tasks complete.
- Peer messages are durable before delivery and arrive with matching ids.
- Integration reads the root board through its parent team scope.
- Integration creates and completes one task on its own board.
- The nested auditor names integration as its parent and team lead.
- Every roster member has a completed assigned task.
- Every bare wait returns after the corresponding board settles.
- The changed command and the project checks pass.

The episodes region shows the process tree. The trajectory shows concurrent
review and testing followed by integration and its nested audit. The tasks
tab shows the two boards and the exact transitions that produced the final
state.
