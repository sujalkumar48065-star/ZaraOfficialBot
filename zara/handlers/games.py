"""Mini-game commands."""

from .bridge import bind

NAMES = {
    "bet": "bet", "card": "card", "flip": "flip", "roulette": "roulette",
    "bomb": "bomb_game", "join": "bomb_join", "pass": "bomb_pass",
    "truth": "quick_prompt", "dare": "quick_prompt", "roast": "quick_prompt",
    "bluff": "quick_prompt", "hack": "hack_game", "register": "hack_register",
    "guess": "hack_guess", "end": "hack_end",
}


def commands(runtime):
    return bind(runtime, NAMES)