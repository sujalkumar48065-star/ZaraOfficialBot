"""GIF reaction/action commands."""

from .bridge import bind

NAMES = {
    command: "action"
    for command in (
        "love", "crush", "kiss", "hug", "slap", "punch", "bite", "murder",
        "pat", "cuddle", "dance", "highfive", "bonk",
    )
}


def commands(runtime):
    return bind(runtime, NAMES)