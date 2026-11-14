"""Telegram sticker commands."""

from .bridge import bind

NAMES = {"sticker": "sticker_command", "stickerpack": "sticker_packs_command"}


def commands(runtime):
    return bind(runtime, NAMES)