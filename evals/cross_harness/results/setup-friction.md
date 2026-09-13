# Why configuring foe for this evaluation was error prone

Eleven distinct setup failures occurred while getting foe to a state where it
could be measured. They are not eleven problems. This records what they share,
because a configuration that took a day of log reading here will cost other
users the same day.

## A grant tells the kernel what to permit and tells the process nothing

Every grant needs a second fact to be usable, and nothing supplies or checks
that second fact.

| granted | what was still missing | how it appeared |
|---|---|---|
| execute on a toolchain directory | the directory on the shell's search path | the command reported as not found |
| execute on a toolchain | a home directory the toolchain manager can use | it looked for its installation in the workspace and tried to download one |
| write on the workspace | a subprocess able to create a file in a directory it had just created there | a build tool refused, and a check script that treated the refusal as fatal |

The permission model held in all three. What failed was everything that has to
agree with it.

## The environment differs by which tool runs

The shell receives a search path, a home directory, a language and a temporary
directory. A configured executable receives an empty environment and must
rebuild all of it. The two are independent, so the same mistake can be made
twice: the home directory was set to the workspace in the shell and,
separately, in the check script this evaluation wrote, and each had to be
found and repaired on its own.

That independence is also why the check script grew into a wrapper that sets
four variables before running anything. Every configured executable that needs
a working environment must carry its own copy of that wrapper.

## A denial surfaces far from its cause

The sandbox refuses a write and the user reads `could not download file from
static.rust-lang.org`. Every environment defect here was found by reading a
log and asking why a command failed. None was found from an error that named
the sandbox, the grant, or the path that was refused.

## Nothing tries the contract before it runs

The planning command prints what a document resolves to. It does not attempt
anything. It cannot report that a granted executable is unreachable by name,
that the home directory an arm will get is unwritable, or that a subprocess
cannot write the write root. One preflight that actually tried those three
things would have caught all three defects above before any spend, and would
have replaced a day of log reading with a line of output.

## The limits compose in ways that must be computed by hand

A root budget, a per-node model-call allowance where "unlimited" means reserve
the remainder, a maximum number of firings, a retry count per node, a second
retry count at the root, and a check timeout derived from the episode's
seconds. Whether these fit together is not visible in the document. The worst
case here demands 3,060 seconds of verification from an 1,800 second episode,
which took a script to discover.

Nothing reserves budget either. On the non-terminating task both foe arms
spent their whole 1,800 seconds inside a single shell call, 1,554 seconds for
one and 1,618 for the other, and neither had time left to report. Bounding the
check tool to a tenth of the episode did not help, because the arm ran the
same suite through the shell instead, and the shell grants the timeout the
caller asks for up to the deadline. An episode that ends with nothing to say
looks exactly like an episode with nothing to say.

## What would remove most of it

- Make the environment follow the grants, and use one rule for every tool
  rather than a different one per tool. Two of the three defects above are
  this.
- Name the sandbox, the grant and the refused path in the denial.
- Add a preflight that runs the contract's own tools and reports what cannot
  be reached.
- Hold a reserve, so that one tool call cannot consume an episode.

The first is done for the shell. The other three are open, and the preflight
is the one to build first: it converts every defect of this class from an
afternoon of log reading into a line of output.
