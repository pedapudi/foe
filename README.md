# foe

**The friendly coding agent.**

foe runs coding work autonomously — no human in the loop — as bounded,
reproducible episodes. A small Rust core owns the episode loop, an
append-only log, capability grants, and forking; everything else is a
capability you grant explicitly.

A *foe* is 10^51 ergs — fifty-one ergs — the energy released by a single
core-collapse supernova. One bounded event, one unit of work.

## Status

Early design. Nothing here is stable.

## Design

- **Nothing by default.** Every capability is an explicit, typed grant.
- **Every request is a pure function of the log.** Full request headers,
  including tool schemas, are recorded so any run replays byte-exact.
- **Effects are declared and enforced.** A tool states what it reads,
  writes, and spawns at registration; the runtime rejects calls that
  exceed the declaration.
- **Episodes fork.** Candidate generation branches from a shared prefix,
  so branches are causally independent and the prefix is paid once.
- **Small core.** Under 5 MB, with zero built-in tools.
