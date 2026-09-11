"""Record a deterministic team conversation through a supplied executable."""

import argparse
import asyncio
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from foe import Completed
from foe._versions import CONFIG_VERSION
from foe._host import start_config


async def record(binary: Path, destination: Path) -> None:
    steps: dict[str, int] = defaultdict(int)
    questions: dict[str, str] = {}
    tasks: set[str] = set()

    def event(item):
        if item.type == "team/task":
            tasks.add(item.data["task_id"])

    async def model(request):
        role = next(name for name in ("Coordinator", "Editor", "Tester") if f"Role: {name}." in request["system"])
        steps[role] += 1
        step = steps[role]
        transcript = json.dumps(request["messages"])
        ids = re.findall(r"Answer this with send, reply_to ([A-Za-z0-9_:]+)\.", transcript)
        call = None
        if step == 1 and ((role == "Editor" and "Begin the review." in transcript) or (role == "Tester" and ids)):
            step = steps[role] = 2
        if role == "Coordinator":
            if step == 1:
                calls = [("spawn", {"contract": name.lower(), "name": name, "task": f"Role: {name}. Review the section."}) for name in ("Editor", "Tester")]
            elif step == 2:
                calls = [("send", {"to": "Editor", "scope": "led", "content": "Begin the review."})]
            elif step == 3:
                calls = [("wait", {})]
            else:
                calls = []
        elif role == "Editor":
            if step == 1:
                call = ("wait", {"until": [{"inbox": "peer"}], "timeout_seconds": 10})
            elif step == 2:
                call = ("notify", {"content": "Checking the section."})
            elif step == 3:
                call = ("ask", {"to": "Tester", "content": "Can this wording ship?", "deadline_ms": 10000, "default": "Keep the draft."})
            elif step == 4:
                matches = re.findall(r"ep_[a-z0-9]+:tm_[0-9]+", transcript)
                questions[role] = matches[-1]
                call = ("wait", {"until": [{"reply": questions[role]}], "timeout_seconds": 10})
            elif step == 5:
                call = ("ask", {"to": "lead", "content": "Publish the summary?", "deadline_ms": 50, "default": "Keep the summary local."})
            elif step == 6:
                matches = re.findall(r"ep_[a-z0-9]+:tm_[0-9]+", transcript)
                call = ("wait", {"until": [{"reply": matches[-1]}], "timeout_seconds": 10})
            calls = [call] if call else []
        else:
            if step == 1:
                call = ("wait", {"until": [{"inbox": "request"}], "timeout_seconds": 10})
            elif step == 2:
                call = ("send", {"to": "Editor", "reply_to": ids[-1], "content": "The wording is ready."})
            elif step == 3:
                call = ("send", {"to": "Editor", "content": "Check the title."})
            calls = [call] if call else []
        for index, (name, args) in enumerate(calls):
            identifier = f"call_{step}_{index}"
            yield {"kind": "tool_call_start", "id": identifier, "name": name}
            yield {"kind": "tool_call_delta", "id": identifier, "delta": json.dumps(args)}
            yield {"kind": "tool_call_end", "id": identifier}
        if not calls:
            text = f"{role} finished."
            if role == "Coordinator":
                text += " Settled tasks: " + ", ".join(sorted(tasks)) + "."
            yield {"kind": "text", "delta": text}
        yield {"kind": "done", "stop": "tool" if calls else "end", "usage": {"input": 10, "output": 5, "cache_read": 0}}

    with tempfile.TemporaryDirectory(prefix="foe-communication-") as temporary:
        def contract(name):
            return {"name": name, "instructions": {"role": f"Role: {name}."}, "tools": ["wait", "send", "ask", "notify"], "grants": {"read": [temporary]}, "budget": {"model_calls": 16, "seconds": 30}}
        config = contract("Coordinator")
        config["version"] = CONFIG_VERSION
        config["sandbox"] = {"mode": "off"}
        config.update(task="Review the section together.", child_contracts={name.lower(): contract(name) for name in ("Editor", "Tester")})
        config["tools"].append("spawn")
        config["grants"]["spawn"] = ["editor", "tester"]
        config["budget"].update(model_calls=64, max_concurrent=3, max_episodes=4, max_depth=2)
        handle = await start_config(config, binary=binary, log_dir=Path(temporary) / "logs", model_backend=model, on_event=event)
        outcome = await handle.wait()
        assert isinstance(outcome, Completed), outcome
        assert handle.log_dir is not None
        destination.mkdir(parents=True, exist_ok=True)
        for path in handle.log_dir.rglob("episode.jsonl"):
            events = [json.loads(line) for line in path.read_text().splitlines()]
            name = events[0]["data"]["contract"]["name"].lower()
            assert events[-1]["data"]["outcome"]["kind"] == "completed", events[-1]
            output = destination / f"{name}.jsonl"
            assert not output.exists(), f"fixture already exists: {output}"
            # Absolute scratch paths have no role in the communication projection.
            content = "\n".join(json.dumps(item, separators=(",", ":")) for item in events)
            assert temporary in content
            output.write_text(content.replace(temporary, "/fixture") + "\n")
        print(outcome)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    asyncio.run(record(args.binary.resolve(), args.destination.resolve()))
