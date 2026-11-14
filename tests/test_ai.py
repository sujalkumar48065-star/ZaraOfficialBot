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
