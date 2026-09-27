"""Greetings for the command-line front end."""


def greet(name):
    """Return the greeting for a name; an empty name greets a stranger."""
    trimmed = name.strip()
    return "Hello, " + (trimmed or "stranger") + "!"
