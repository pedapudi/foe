# Recorded team communication

These logs come from a deterministic execution through the built binary.
The coordinator starts an editor and a tester. They exchange questions,
replies, a notification, and ordinary messages. An unanswered question
receives its deadline default. All three episodes complete successfully.

The browser and terminal tests read the same logs. Scratch directory paths
are replaced with `/fixture`; identifiers, event order, timestamps, and
payloads retain their recorded values. No log schema fields are added.

To record another fixture, prepare the Python environment with
`uv sync --all-extras` in `python/`, then run from the repository root:

```sh
python/.venv/bin/python view/fixtures/communication/generate.py /absolute/path/to/foe /absolute/path/to/empty-directory
```

The destination must contain none of the three output log files.
