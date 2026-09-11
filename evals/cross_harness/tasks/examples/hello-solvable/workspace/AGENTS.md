# Working in this repository

The code lives under `src/` and the tests under `tests/`. Change files under
`src/` only. The tests and this file describe the expected behavior and stay
as they are.

Run the checks from the repository root:

```sh
/usr/bin/python3 -B -m unittest discover -s tests -t .
```

Every test passes before the work is reported as done.
