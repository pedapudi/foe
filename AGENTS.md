# Working in this repository

Rules for anyone, human or agent, changing this repository.

## Read first

`docs/design.md` is the design. `docs/log-format.md`, `docs/protocol.md`,
`docs/config.md`, `docs/workflow.md`, and `docs/compaction.md` are
specifications; code implements them and does not reinterpret them. When
code and a specification disagree, the specification is wrong only if a
written change to it is made in the same commit.

## Prose

All prose in this repository follows one standard: a competent reader who
has the repository and none of its development history understands every
sentence on first reading.

- Present-state specification. No narration of how something came to be.
- Explain a concept in plain words before giving its name.
- One idea per sentence. No sentence over about 40 words.
- No contractions. No rhetorical questions. No metaphors, epigrams, or
  colloquialisms. No empty intensifiers.
- No antithesis of the form "X, not Y" in any position. State what a thing
  is. When the negation carries content, use "rather than" in an ordinary
  clause.
- No invented codenames, lifecycle labels used as names (`v2`, `new`,
  `legacy`), or opaque abbreviations. Every name says what the thing is.
- Every "N things" claim matches the list it announces.
- Comments state invariants, intent, and constraints that the code does not
  make visible. A comment that restates the line below it is removed. A TODO
  names the missing condition and the criterion for removing it.

## Code

- Rust 2021, toolchain pinned in `rust-toolchain.toml`. `cargo fmt` and
  `cargo clippy -- -D warnings` pass before every commit.
- `crates/log` depends on serde, serde_json, and thiserror, and on no
  crate of this repository. The only crate of this repository that
  `crates/core` depends on is `crates/log`. Nothing depends on
  `crates/view` except the binary.
- No environment variable is read anywhere. Configuration arrives as a file.
- No path list is searched. Executables are named by absolute path.
- Every error names the key, event, or rule involved.
- Tests live beside the code they test. A specification rule that can be
  tested has a test that cites the rule.
- The suite has two tiers. `cargo test --workspace` is the fast one and
  waits on no real clock: a test of a rule about elapsed time runs on
  tokio's virtual clock, under `#[tokio::test(start_paused = true)]`.
  `scripts/examples.sh` is the slow one and runs every example against a
  built binary. Continuous integration runs both. See docs/build.md
  "The two tiers of the test suite".
- Rust line budgets exclude tests, generated code, blank lines, and
  comment-only lines. `log` and `core` form the kernel and stay under 6,350
  lines together. `contract` stays under 1,575 and `code` under 1,900. `team`
  stays under 925. Coding tools and team coordination together stay under
  2,750. `workflow` stays under 1,050, `context` under 500, `view` under 900,
  `cli` under 2,025, `transport` under 2,700, `telemetry` under 1,000, and
  `evidence` under 500. The viewer HTML, TypeScript, and CSS use the
  compressed bundle limit in `docs/design.md`. `scripts/loc.sh` enforces every
  Rust line budget and fails when this file, `README.md`, or `docs/design.md`
  quotes a ceiling that differs from the one it holds.
- A ceiling changes in a commit of its own. That commit states the reason
  and touches only `AGENTS.md`, `scripts/loc.sh`, `README.md`, and
  `docs/design.md`, all four, and precedes the commit that needs the room,
  so every commit in a series passes `scripts/loc.sh`. A feature commit
  never changes a ceiling. A ceiling may be lowered as well as raised.
- A raise is a decision about how much of a surface a reader can hold at
  once, so a commit that makes one says why this behavior is worth the room
  and where the room was looked for first. It names what the surface already
  holds that the change could have replaced, deleted, or reused, and why none
  of it served. "The ceiling was in the way" is the finding that prompts the
  question, not an answer to it. A raise nobody can argue with is one nobody
  examined.
- A group ceiling bounds surfaces the total already counts, so it never
  exceeds the sum of the ceilings it bounds; past that sum it states nothing,
  and `scripts/loc.sh` refuses it. Raising it toward that sum loosens how
  closely the group holds its surfaces together, and the reason it gives is
  why they no longer need holding that closely. A raise that would carry a
  group over its own ceiling moves the group in the same commit; the group is
  the tighter of the two and it is what refuses the change until it does.
- A change that raises a ceiling, or that adds a name the repository then
  keeps, states six things. A name is kept when removing it later breaks
  something already written down: a log the viewer replays, a contract
  document a user holds, a configuration file, or a command a script calls.
  Log events and their fields, contract-document keys, tool names, and
  commands are all such names. The six:
  - the failure a user or a model meets without the change, named as an
    example, a test, or an issue that reproduces it;
  - the contract that would otherwise carry the behavior — a contract
    document, a tool, a workflow, or the log — and the property it lacks;
  - the code or the concept the change removes;
  - the line count of every budgeted surface after the change, as
    `scripts/loc.sh` reports it;
  - the tests and the specification rules that define the added behavior; and
  - the condition under which the behavior is removed again, stated as
    something observable such as a closed issue or a retired key.
  The ceiling commit states the six for a raise, since it precedes the change
  that needs the room. Otherwise the commit that adds the name states them. A
  pull request states them once for the series, with the production lines it
  adds, the production lines it removes, and the kept names it adds and
  removes.
- A reviewer checks the six statements. Each names what the reviewer runs,
  reads, or contradicts. The example fails without the change.
  The removed code appears in the diff. The named tests pass. A configuration
  that expresses the behavior contradicts the claim that no existing contract
  can. A statement that names nothing the reviewer can reach is unanswered,
  and the ceiling does not move.

## Commits

- One change per commit. The message states the resulting behavior in the
  imperative, then the reason when it is not obvious.
- No trailers of any kind. No co-author lines. No links to sessions or
  tools. No mention of any AI system anywhere in the repository.
- A commit that touches more than five files, or that renames anything,
  carries a body. The body states the resulting behavior, the reason, the
  compatibility impact, and the validation performed.
- A rename is its own commit and changes no behavior.
- A commit that breaks compatibility names the error an old input now
  receives and the document that records the break.
- Never force-push. Never commit to `main` from a worktree; open a branch.
- Work happens on a branch. A branch reaches `main` in one of two ways: it
  is rebased onto `main`, or it is merged into `main` through a pull
  request. Squash merge is the default for a branch an agent produced.
- `main` is never merged into a branch that is then pushed as `main`.
- A series is integrated once. The branch it came from is deleted after
  integration.
- A merge commit carries nothing beyond the automatic merge. A conflict is
  resolved in an ordinary commit on the branch, and that commit's message
  states what the resolution changed.
- Every gate runs on the merged tree before a push: `cargo test
  --workspace`, `scripts/loc.sh`, `scripts/examples.sh`, the Python suite
  in `python/`, and the browser bundle build and test suite in `view/`.
- `main` requires the continuous-integration checks to pass.

## Verification

A change is done when it builds, its tests pass, clippy is clean, the
documents it affects are updated in the same commit, and, for user-facing
behavior, the behavior was exercised once by hand against the built binary.
