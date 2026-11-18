from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatType

import bot as zara_bot
from zara import ai


def test_full_zarabest_prompt_is_loaded():
    assert "real 21-year-old Indian girl" in ai.SYSTEM_PROMPT
    assert "MATCH THE USER'S LANGUAGE" in ai.SYSTEM_PROMPT
    assert len(ai.SYSTEM_PROMPT) > 8000


def test_language_detection():
    assert ai.detect_language("kya kar rahi ho") == "hinglish"
    assert ai.detect_language("how are you") == "english"
    assert ai.detect_language("तुम कैसी हो") == "hindi"


def test_private_chat_always_responds():
    assert ai.should_respond(
        "hello", chat_type="private", is_reply_to_bot=False
    )


def test_group_needs_reply_mention_or_name():
    assert not ai.should_respond(
        "hello", chat_type="group", is_reply_to_bot=False
    )
    assert ai.should_respond(
        "hello", chat_type="group", is_reply_to_bot=True
    )
    assert ai.should_respond(
        "zara kaha ho", chat_type="group", is_reply_to_bot=False
    )
    assert ai.should_respond(
        "@Meee_Zarabot kaha ho",
        chat_type="group",
        is_reply_to_bot=False,
        bot_username="Meee_Zarabot",
    )


def test_sticker_only_dm_or_reply_to_bot():
    assert ai.should_respond(
        "", chat_type="private", is_reply_to_bot=False, is_sticker=True
    )
    assert ai.should_respond(
        "", chat_type="group", is_reply_to_bot=True, is_sticker=True
    )
    assert not ai.should_respond(
        "", chat_type="group", is_reply_to_bot=False, is_sticker=True
    )


def test_conversation_memory_keeps_recent_turns():
    memory = ai.ConversationMemory(ttl=60, max_messages=6)
    memory.store(1, 2, "user", "hello")
    memory.store(1, 2, "assistant", "heyy")
    history = memory.history(1, 2)
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "heyy"},
    ]


def test_provider_order_prefers_grok(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("XAI_MODEL", "grok-4")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    providers = ai._provider_configs()
    assert [provider[0] for provider in providers] == [
        "grok",
        "openrouter",
        "openai",
    ]
    assert providers[0][1] == "https://api.x.ai/v1/chat/completions"
    assert providers[0][3] == "grok-4"


def test_system_prompt_uses_personality_and_language():
    prompt = ai.build_system_prompt(
        personality="sassy", length="short", language="hinglish"
    )
    assert "sassy" in prompt.lower()
    assert "VERY short" in prompt
    assert "Hinglish" in prompt


def test_local_fallback_keeps_ai_chat_alive():
    assert ai.fallback_reply("hello", "english").startswith("heyy")
    assert "kaal" not in ai.fallback_reply("kaise ho", "hinglish")
    assert "kya hua" in ai.fallback_reply("main sad hu", "hinglish")
    assert ai.fallback_reply("random text", "english")


def test_groq_endpoint_gets_fallback_model(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "gsk-test")
    monkeypatch.setenv("XAI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("XAI_MODEL", "qwen/qwen3.8-27b")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    models = [item[3] for item in ai._provider_candidates()]
    assert models == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]


@pytest.mark.asyncio
async def test_ai_reply_sends_local_fallback_when_generation_is_empty(monkeypatch):
    monkeypatch.setattr(ai, "has_ai_key", lambda: True)
    monkeypatch.setattr(ai, "generate_reply", AsyncMock(return_value=None))
    message = SimpleNamespace(text="hello", reply_to_message=None, reply_text=AsyncMock())
    user = SimpleNamespace(id=2, first_name="Riya", username="riya", is_bot=False)
    chat = SimpleNamespace(id=-1001, type=ChatType.SUPERGROUP)
    update = SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

    await zara_bot.ai_reply(update, context, text="hello")

    message.reply_text.assert_awaited_once()
    assert "ʜᴇʏʏ" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_ai_reply_sends_fallback_when_generation_raises(monkeypatch):
    monkeypatch.setattr(ai, "has_ai_key", lambda: True)
    monkeypatch.setattr(
        ai, "generate_reply", AsyncMock(side_effect=RuntimeError("provider down"))
    )
    message = SimpleNamespace(text="hello", reply_to_message=None, reply_text=AsyncMock())
    user = SimpleNamespace(id=2, first_name="Riya", username="riya", is_bot=False)
    chat = SimpleNamespace(id=-1001, type=ChatType.SUPERGROUP)
    update = SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

    await zara_bot.ai_reply(update, context, text="hello")

    message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_reply_without_key_still_answers(monkeypatch):
    monkeypatch.setattr(ai, "has_ai_key", lambda: False)
    reply = AsyncMock()
    monkeypatch.setattr(zara_bot, "safe_reply", reply)
    message = SimpleNamespace(text="hello", reply_to_message=None, reply_text=AsyncMock())
    user = SimpleNamespace(id=2, first_name="Riya", username="riya", is_bot=False)
    chat = SimpleNamespace(id=-1001, type=ChatType.SUPERGROUP)
    update = SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

    await zara_bot.ai_reply(update, context, text="hello")

    reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_reply_timeout_uses_fallback(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "gsk-test")
    monkeypatch.setenv("XAI_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class ImmediateTimeout:
        async def __aenter__(self):
            raise TimeoutError

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(ai.asyncio, "timeout", lambda seconds: ImmediateTimeout())

    result = await ai.generate_reply(text="hello", chat_id=1, user_id=2)

    assert result is not None
    assert result.provider == "local"
