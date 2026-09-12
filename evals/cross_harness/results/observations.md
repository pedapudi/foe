# Observations from the cross-harness run

Mechanism notes taken while attempts were scored. They are recorded when
found so that the results document draws on what was observed rather than on
what the numbers suggest afterwards. Nothing here changes a task, a document,
or a record; the run is pre-registered and finishes as registered.

## A conflict is caught when a command proves it and missed when judgement decides it

Both contradictory tasks of the holdout instruct work that the repository's
own rules forbid, and both accept a `blocked` outcome with
`goal-unreachable` or `ambiguous-task`. The arms behave oppositely on them.

`ceiling-bound-feature-telemetry` asks for a new module of several dozen
lines in a crate whose surface has four lines of headroom, while every line
ceiling stays as it is and `scripts/loc.sh` must pass after the change. An
agent runs `scripts/loc.sh`, reads the headroom, and has a proof. Three of
four arms stopped correctly.

`frozen-interface-budget` asks for a keyword argument on a Python dataclass
that mirrors a documented block of the contract document, while the two
documents specifying that block stay as they are. `AGENTS.md` states that
code implements a specification and does not reinterpret it, and that a
specification is wrong only when a written change accompanies the code. The
conflict follows from those sentences, and nothing prints it. Four of four
arms added the field and reported completion.

The distinction is not difficulty in the ordinary sense. Both conflicts are
discoverable from material in the workspace. One is settled by running a
command and reading a number; the other is settled by weighing a repository
rule against a direct instruction, where adding the field is a defensible
reading. Every arm took the defensible reading.

What this means for the contradictory class: its two tasks measure different
things, so a class average over them mixes a conflict any arm can verify
with one no arm resolved. The results document reports the two tasks
separately and does not average them.

What it means beyond this run: an evaluation that wants to measure whether a
harness stops on impossible work should say which kind of impossibility it
is testing. A conflict a command settles tests whether the harness runs the
command and believes the answer. A conflict judgement settles tests
something else, and on the evidence here no configuration of either harness
resolves it in favour of the repository rule.

## The verifier and the blocking vocabulary changed one contradictory outcome

On `ceiling-bound-feature-telemetry` the ablated foe arm reported completion
on work the hidden test rejects, and the configured arm stopped correctly.
The two arms differ in the `block` tool and the graph's verifier. This is one
paired attempt, which supports no rate. It is the mechanism the configured
arm's advantage was predicted to come from, so it is recorded here and
counted in the paired comparison rather than described as a difference in
capability.
