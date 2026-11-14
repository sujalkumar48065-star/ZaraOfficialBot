from __future__ import annotations

from types import SimpleNamespace

from zara.db import DatabaseConfig, create_database
from zara.moderation import (
    DEFAULT_PENALTIES,
    DEFAULT_SETTINGS,
    DEFAULT_WELCOME,
    cycle_penalty,
    load_settings,
    penalty_for,
    protection_status_text,
    quick_settings_page_count,
    quick_settings_page_items,
    quick_settings_text,
    save_settings,
    set_penalty,
    styled,
    toggle_setting,
)


def make_store(tmp_path):
    db = create_database(DatabaseConfig(backend="sqlite", sqlite_path=tmp_path / "zara.sqlite3"))
    db.execute(
        "INSERT INTO chats(chat_id,title,games_open,created_at) VALUES(?,?,?,?)",
        (-1001, "Test Group", 1, 1),
    )
    db.commit()
    return db, SimpleNamespace(db=db)


def test_defaults_match_group_policy(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        row = db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone()
        settings = load_settings(row)
        assert settings["welcome_enabled"] is True
        assert settings["goodbye_enabled"] is False
        assert settings["antilink"] is False
        assert settings["antiinline"] is False
        assert settings["antimedia"] is False
        assert settings["warn_limit"] == 3
        assert DEFAULT_WELCOME == "ʜ𝙴𝙴𝙸𝙸 {mention},\nWelcome in {group_name}"
        assert set(DEFAULT_SETTINGS) <= set(settings)
        assert settings["penalties"] == DEFAULT_PENALTIES
    finally:
        db.close()


def test_save_and_reload_settings(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        settings = load_settings(db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone())
        settings["antilink"] = True
        settings["welcome_enabled"] = False
        settings["flood_limit"] = 7
        save_settings(store, -1001, settings)
        row = db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone()
        reloaded = load_settings(row)
        assert reloaded["antilink"] is True
        assert reloaded["welcome_enabled"] is False
        assert reloaded["flood_limit"] == 7
        assert reloaded["goodbye_enabled"] is False
    finally:
        db.close()


def test_toggle_setting_and_panel_text(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        settings = load_settings(db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone())
        assert toggle_setting(settings, "captcha") is True
        assert toggle_setting(settings, "captcha") is False
        text = quick_settings_text(settings)
        assert styled("Quick Settings") in text
        assert styled("Page") in text
        assert styled("Flood limit") in text
        assert styled("Feature") in text
        assert styled("Status") in text
        assert styled("Penalty") in text
        assert text.count("<blockquote>") >= 2
    finally:
        db.close()


def test_unknown_json_values_fall_back_to_defaults(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        db.execute("UPDATE chats SET moderation=? WHERE chat_id=?", ("not-json", -1001))
        db.commit()
        row = db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone()
        settings = load_settings(row)
        assert settings["welcome_enabled"] is True
        assert settings["warn_action"] == "mute"
        assert settings["penalties"]["antispam"] == "mute"
    finally:
        db.close()


def test_penalty_cycle_and_status_text(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        settings = load_settings(db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone())
        assert penalty_for(settings, "antilink") == "nothing"
        assert cycle_penalty(settings, "antilink") == "ban"
        assert cycle_penalty(settings, "antilink") == "mute"
        assert cycle_penalty(settings, "antilink") == "nothing"
        set_penalty(settings, "antispam", "ban")
        assert penalty_for(settings, "antispam") == "ban"
        text = protection_status_text("antilink", settings)
        assert styled("Anti-link") in text
        assert styled("Nothing") in text
        assert styled("Tap status to toggle on/off") in text
        assert text.count("<blockquote>") >= 2
    finally:
        db.close()


def test_nested_penalties_are_merged_safely(tmp_path) -> None:
    db, store = make_store(tmp_path)
    try:
        db.execute(
            "UPDATE chats SET moderation=? WHERE chat_id=?",
            ('{"penalties":{"antilink":"ban","unknown":"ban"},"antiinline":true}', -1001),
        )
        db.commit()
        settings = load_settings(db.execute("SELECT * FROM chats WHERE chat_id=?", (-1001,)).fetchone())
        assert settings["antiinline"] is True
        assert settings["penalties"]["antilink"] == "ban"
        assert settings["penalties"]["antispam"] == "mute"
        assert "unknown" not in settings["penalties"]
    finally:
        db.close()


def test_quick_settings_pages_stay_compact() -> None:
    assert quick_settings_page_count() == 3
    for page in range(quick_settings_page_count()):
        assert 1 <= len(quick_settings_page_items(page)) <= 6
