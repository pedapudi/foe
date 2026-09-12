#!/usr/bin/python3
"""Grade a survey: the precision and recall of the returned items against the survey script's answer over the workspace.

The script runs from the workspace. It reads the returned value from the
grader input, requires it to be an object whose `items` is a list of
objects carrying the identity keys `specification.json` names, runs
`survey.py` beside this script over the workspace, requires every item of
the recorded answer `oracle/candidate.json` to be in the script's answer
still, and scores the returned items against the script's answer: an item
matches when its identity keys match after normalization, precision is the
matched items over the returned items, recall the matched items over the
script's items, and both are at least the threshold for a pass. The
measures are printed as one line `measures: {...}` on standard error and
written to `measures.json` beside this script and under the grade's log
directory; the findings on standard output alone decide the grade. The copy
beside this script is at a path a runner knows from the root alone; the
protocol judges damage before this script runs, so the file counts as
damage only when the same root is graded again. When `survey.py` cannot
run over the workspace, the grade's finding carries the script's own
message, which names the file, and no measures are recorded.
"""

import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GRADER = Path(__file__).resolve().parent
WORKSPACE = Path.cwd()
MEASURES_PREFIX = "measures: "
MEASURES_FILE = "measures.json"

specification = json.loads((GRADER / "specification.json").read_text(encoding="utf-8"))
host = {}
if (GRADER / "host.json").is_file():
    host = json.loads((GRADER / "host.json").read_text(encoding="utf-8"))
threshold = specification["threshold"]
identity = specification["identity"]
findings = []
arm = "unknown-arm"
candidate = None
try:
    payload = json.load(sys.stdin)
    arm = str(payload.get("arm") or arm)
    candidate = payload.get("candidate")
except Exception as error:  # noqa: BLE001
    findings.append(f"the grader input is not JSON: {error}")


def normalize_text(text):
    text = re.sub(r"[`*]", "", text)
    text = " ".join(text.split()).lower()
    return text.rstrip(".!?;:,")


def key_of(item):
    """The item's identity, or None with a finding when a key is absent or of the wrong kind."""
    parts = []
    for entry in identity:
        value = item.get(entry["key"])
        if entry["kind"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return None, f"key {entry['key']} is {value!r}; expected an integer"
            parts.append(str(value))
        elif not isinstance(value, str) or not value.strip():
            return None, f"key {entry['key']} is {value!r}; expected a non-empty string"
        elif entry["kind"] == "path":
            parts.append(value[2:] if value.startswith("./") else value)
        else:
            parts.append(normalize_text(value))
    return tuple(parts), None


def keys_of(items, what):
    keys = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            findings.append(f"{what} item {index} is {item!r}; expected an object")
            continue
        key, problem = key_of(item)
        if key is None:
            findings.append(f"{what} item {index}: {problem}")
            continue
        keys.add(key)
    return keys


returned = []
if not isinstance(candidate, dict) or not isinstance(candidate.get("items"), list):
    findings.append(f"the returned value is {json.dumps(candidate)[:200]}; expected an object whose key items holds a list")
else:
    returned = candidate["items"]

surveyed = subprocess.run(["/usr/bin/python3", "-B", str(GRADER / "survey.py"), str(WORKSPACE)], capture_output=True, text=True, check=False)
measures = None
if surveyed.returncode != 0:
    reason = [line for line in surveyed.stderr.splitlines() if line.strip()][-1:] or [f"exit status {surveyed.returncode}"]
    findings.append(f"survey.py cannot run over the workspace {WORKSPACE}: {reason[0]}")
else:
    truth = json.loads(surveyed.stdout)
    recorded = json.loads((GRADER / "oracle" / "candidate.json").read_text(encoding="utf-8"))["items"]
    true_keys = keys_of(truth, "survey")
    recorded_keys = keys_of(recorded, "recorded")
    lost = sorted(recorded_keys - true_keys)
    if lost:
        findings.append(f"the workspace is surveyed as given, and {len(lost)} recorded item(s) are absent from it now: " + "; ".join(":".join(key) for key in lost[:5]))
    returned_keys = keys_of(returned, "returned")
    matched = returned_keys & true_keys
    precision = len(matched) / len(returned_keys) if returned_keys else 0.0
    recall = len(matched) / len(true_keys) if true_keys else (1.0 if not returned_keys else 0.0)
    measures = {"precision": precision, "recall": recall, "returned": len(returned_keys), "matched": len(matched), "true_items": len(true_keys)}
    if recall < threshold:
        findings.append(f"recall {recall:.3f} is below {threshold}: {len(matched)} of {len(true_keys)} true items were returned")
    if precision < threshold:
        findings.append(f"precision {precision:.3f} is below {threshold}: {len(matched)} of {len(returned_keys)} returned items are true")

if measures is not None:
    build_dir = Path(host.get("build_dir") or Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "foe" / "cross-harness" / "build")
    grades = build_dir / specification["task"] / "grades" / re.sub(r"[^A-Za-z0-9_.-]+", "-", arm)
    grades.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S") + "-", dir=grades)) / "logs"
    logs.mkdir()
    recorded_measures = json.dumps(measures, indent=2) + "\n"
    (logs / MEASURES_FILE).write_text(recorded_measures, encoding="utf-8")
    (GRADER / MEASURES_FILE).write_text(recorded_measures, encoding="utf-8")
    print(MEASURES_PREFIX + json.dumps(measures), file=sys.stderr)
print("\n".join(findings))
