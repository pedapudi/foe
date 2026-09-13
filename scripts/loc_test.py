"""Exercise the counting rule loc.sh applies, on files written for the test."""

import subprocess
import tempfile
import unittest
from pathlib import Path

COUNTER = Path(__file__).with_name("production_lines.awk")

PRODUCTION = """pub fn one() -> u32 {
    1
}

pub fn two() -> u32 {
    2
}
"""

TESTS = """#[cfg(test)]
mod tests {
    #[test]
    fn t() {}
}
"""


def counted(source: str) -> int:
    """The production lines the counter reports for one file of `source`."""
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "lib.rs"
        path.write_text(source)
        report = subprocess.run(
            ["awk", "-f", str(COUNTER), str(path)], capture_output=True, text=True, check=True
        )
        return int(report.stdout.strip())


class Counter(unittest.TestCase):
    def test_an_inline_test_module_is_not_counted(self) -> None:
        self.assertEqual(counted(PRODUCTION + "\n" + TESTS), 6)

    def test_production_code_after_a_test_module_is_counted(self) -> None:
        """Skipping from a test module to the end of the file would leave
        every later line uncounted, so a file that opens with its tests would
        declare nothing and pass any ceiling."""
        self.assertEqual(counted(TESTS + "\n" + PRODUCTION), 6)

    def test_a_test_module_between_two_others_is_skipped_once(self) -> None:
        self.assertEqual(counted(PRODUCTION + "\n" + TESTS + "\n" + PRODUCTION), 12)

    def test_comments_and_blank_lines_are_not_counted(self) -> None:
        self.assertEqual(counted("// a comment\n\n" + PRODUCTION), 6)

    def test_a_module_named_tests_without_the_attribute_is_production(self) -> None:
        self.assertEqual(counted("mod tests {\n    pub fn f() {}\n}\n"), 3)


if __name__ == "__main__":
    unittest.main()
