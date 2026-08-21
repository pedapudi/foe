# Working in this repository

Rules for anyone, human or agent, changing this repository.

## Read first

`docs/design.md` is the design. `docs/log-format.md`, `docs/protocol.md`,
and `docs/config.md` are specifications; code implements them and does not
reinterpret them. When code and a specification disagree, the specification
is wrong only if a written change to it is made in the same commit.

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
- `crates/log` depends on serde and nothing else. `crates/core` depends on
  `crates/log`. Nothing depends on `crates/view` except the binary.
- No environment variable is read anywhere. Configuration arrives as a file.
- No path list is searched. Executables are named by absolute path.
- Every error names the key, event, or rule involved.
- Tests live beside the code they test. A specification rule that can be
  tested has a test that cites the rule.
- Line budget: `log`, `core`, `code`, and `view` together stay under 6,000
  lines of Rust excluding tests and generated code. `scripts/loc.sh` counts.

## Commits

- One change per commit. The message states the resulting behavior in the
  imperative, then the reason when it is not obvious.
- No trailers of any kind. No co-author lines. No links to sessions or
  tools. No mention of any AI system anywhere in the repository.
- Never force-push. Never commit to `main` from a worktree; open a branch.

## Verification

A change is done when it builds, its tests pass, clippy is clean, the
documents it affects are updated in the same commit, and, for user-facing
behavior, the behavior was exercised once by hand against the built binary.
