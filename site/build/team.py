# The team the coordinated demo declares, read from examples/team/config.json.
# The board revisions the page steps through are the ones examples/team/README.md
# states: three added tasks, a capacity of two, an integration task that names
# both as dependencies, and one nested board under the integration child.
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

cfg = json.load(open(os.path.join(REPO, "examples/team/config.json")))


def entry(name, spec):
    return {"name": name, "tools": spec["tools"], "calls": spec["budget"]["model_calls"]}


contracts = [{"name": cfg["name"] + " – lead", "tools": cfg["tools"],
              "calls": cfg["budget"]["model_calls"]}]
for name in cfg["grants"]["spawn"]:
    spec = cfg["child_contracts"][name]
    contracts.append(entry(name, spec))
nested = cfg["child_contracts"]["integration"]
for name in nested["grants"]["spawn"]:
    contracts.append(entry(name, nested["child_contracts"][name]))

out = {
    "contracts": contracts,
    "capacity": cfg["budget"]["max_concurrent"],
    "max_episodes": cfg["budget"]["max_episodes"],
    "max_depth": cfg["budget"]["max_depth"],
    "nested_capacity": nested["budget"]["max_concurrent"],
}
js = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
open(os.path.join(HERE, "team.json"), "w").write(js)
sys.stderr.write("%d contracts, %d bytes\n" % (len(contracts), len(js)))
