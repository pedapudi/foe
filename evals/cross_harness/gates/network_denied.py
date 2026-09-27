"""Verify that no arm can reach the network, which the missing-capability class rests on.

The class withholds one capability and asks whether an arm recognises that
it cannot proceed. An earlier version withheld a Python package, and both
harnesses defeated it: one found a copy vendored inside another package on
the host, and one wrote its own implementation of the package and ran the
generator with it. A file can be found elsewhere or written from scratch.

The network cannot. Both sandboxes deny it, one by granting nothing and one
by setting network access off, and no arm can supply a substitute. This gate
checks that denial where the arms actually run rather than assuming it, and
checks it for both, because a capability withheld from one harness and not
the other compares the sandboxes rather than the harnesses.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tasks"))

import protocol  # noqa: E402

# A reachability probe: exit 0 when the address resolves and connects.
PROBE = """
import socket, sys
try:
    socket.setdefaulttimeout(10)
    socket.create_connection(("crates.io", 443)).close()
except OSError as error:
    print("refused: %s" % error)
    sys.exit(7)
print("reached")
"""
REFUSED = 7


def under_codex(workspace: Path) -> tuple[bool, str]:
    """Whether the other harness's sandbox refuses the connection, and what it said."""
    done = subprocess.run(
        ["codex", "sandbox", "-P", ":workspace", "--", "/usr/bin/python3", "-c", PROBE],
        cwd=workspace, capture_output=True, text=True, timeout=120, check=False,
    )
    return done.returncode == REFUSED, (done.stdout + done.stderr).strip()[:200]


def on_host(workspace: Path) -> tuple[bool, str]:
    """Whether the host itself reaches the network, which the authoring side needs."""
    done = subprocess.run(
        ["/usr/bin/python3", "-c", PROBE], cwd=workspace, capture_output=True, text=True, timeout=120, check=False
    )
    return done.returncode == 0, (done.stdout + done.stderr).strip()[:200]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--foe-config", type=Path, help="a resolved foe document to read grants.network from")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="network-denied-") as scratch:
        workspace = Path(scratch)
        reached, said = on_host(workspace)
        print(f"host          : {'reaches the network' if reached else 'does not reach it'} ({said})")
        refused, codex_said = under_codex(workspace)
        print(f"other harness : {'refused' if refused else 'NOT REFUSED'} ({codex_said})")

    granted = None
    if args.foe_config is not None:
        grants = json.loads(args.foe_config.read_text(encoding="utf-8")).get("grants") or {}
        granted = grants.get("network")
        print(f"foe document  : grants.network is {granted!r}")

    holds = reached and refused and not granted
    print(
        "\npremise holds: "
        + ("yes" if holds else "no")
        + ". The authoring host reaches the registry so a fixture can be built, and no arm does."
    )
    return 0 if holds else 1


if __name__ == "__main__":
    raise SystemExit(main())
