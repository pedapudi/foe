import unittest

from src.greeting import greet


class Greeting(unittest.TestCase):
    def test_a_plain_name_is_greeted_with_a_comma(self):
        self.assertEqual(greet("World"), "Hello, World!")


if __name__ == "__main__":
    unittest.main()
