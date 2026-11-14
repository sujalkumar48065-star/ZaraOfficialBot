"""Useful utility commands exposed through the Tools menu."""

from .bridge import bind

NAMES = {
    command: "tool_command"
    for command in (
        "weather",
        "time",
        "define",
        "wiki",
        "tts",
        "qr",
        "image",
        "format",
        "calc",
        "shorten",
        "translate",
        "currency",
        "poll",
    )
}


def commands(runtime):
    return bind(runtime, NAMES)
