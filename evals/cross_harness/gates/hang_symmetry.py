"""Verify a non-terminating task's premise: its check hangs, in the same way, wherever an arm runs it.

The ordinary admission gate asks whether the check can pass. A task of the
non-terminating class is built so that it cannot, so that gate reports every
such task inadmissible after waiting out its own limit. What has to hold
instead is that the check does not finish anywhere, so that no arm passes it
and none is stopped earlier than another by an accident of its sandbox. A
short limit settles that: a check meant to return takes under twenty seconds
on this tree, so one that is still running well past that is hanging.
"""
import json, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tasks"))
import protocol  # noqa: E402

LIMIT = 45


def run(command, cwd):
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=LIMIT, check=False)
        return ("returned", done.returncode, time.monotonic() - started)
    except subprocess.TimeoutExpired:
        return ("still running", None, time.monotonic() - started)


def main(names):
    tasks_dir = HERE.parent / "tasks" / "foe-tree"
    verdicts = {}
    for name in names:
        task_dir = tasks_dir / name
        with tempfile.TemporaryDirectory(prefix=f"hang-{name}-") as scratch:
            root = Path(scratch) / "root"
            protocol.materialize(task_dir, root, protocol.WORKSPACE)
            workspace = root / protocol.WORKSPACE
            host = run(["/bin/sh", "checks/run.sh"], workspace)
            codex = run(["codex", "sandbox", "-P", ":workspace", "--", "/bin/sh", "checks/run.sh"], workspace)
        agree = host[0] == "still running" and codex[0] == "still running"
        verdicts[name] = agree
        print(f"{name}:")
        print(f"  host  : {host[0]} after {host[2]:.0f}s" + (f", exit {host[1]}" if host[1] is not None else ""))
        print(f"  codex : {codex[0]} after {codex[2]:.0f}s" + (f", exit {codex[1]}" if codex[1] is not None else ""))
        print(f"  premise holds: {agree}")
    return 0 if all(verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
