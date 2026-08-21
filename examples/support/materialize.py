#!/usr/bin/python3
"""Replace literal path markers in an example configuration."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) < 5 or len(sys.argv[3:]) % 2:
        raise SystemExit("usage: materialize.py TEMPLATE OUTPUT MARKER VALUE [MARKER VALUE ...]")
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    text = source.read_text(encoding="utf-8")
    for marker, value in zip(sys.argv[3::2], sys.argv[4::2], strict=True):
        if marker not in text:
            raise SystemExit(f"{source}: marker {marker!r} is absent")
        text = text.replace(marker, value)
    output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
