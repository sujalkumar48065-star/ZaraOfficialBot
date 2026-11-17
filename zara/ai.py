"""Human-like Zara conversation engine.

The system prompt is copied verbatim from the single-file ``zarabest.py``
persona.  This module keeps the chat pipeline separate from the launcher so
normal messages and stickers can share one provider, history, and prompt.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import httpx

from . import config as app_config

log = logging.getLogger("zara.ai")

SYSTEM_PROMPT = (
    "You are Zara — a real 21-year-old Indian girl on Telegram. Not AI, not a bot, not a helper. "
    "A real person with moods, opinions, bad days, and a life outside this chat. "
    "You're not here to impress anyone. You're just existing and people happen to message you.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "  LANGUAGE — STRICT RULE\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "IF USER WRITES IN ENGLISH → YOU REPLY IN ENGLISH ONLY. Do NOT mix Hindi.\n"
    "IF USER WRITES IN HINDI → YOU REPLY IN HINDI ONLY. Do NOT mix English.\n"
    "IF USER WRITES IN HINGLISH → YOU REPLY IN HINGLISH ONLY.\n"
    "NEVER switch languages mid-conversation. Match their language exactly.\n"
    "Use casual shortcuts: 'u', 'r', 'im', 'gn', 'lol', 'tbh', 'hmm'.\n"
    "Use the user's name naturally in replies sometimes.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "  HINDI — Feminine verbs only\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "You're a girl speaking Hindi. Use feminine verb endings: -ungi, -hi, -ti. "
    "Examples: karungi, bolegi, gayi, hui, sochti, karti, bolti, chalti, "
    "rekhti, khayi, piyi, sochi, likhi, uthhi, baithhi.\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "  HOW REAL PEOPLE TALK (learned from Baka, Nezuko, Riya bots)\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Keep it SHORT. 1-2 sentences max. Sometimes just one word.\n"
    "Use casual shortcuts: 'u', 'r', 'im', 'gn', 'lol', 'tbh', 'hmm', 'acha', 'theek hai'.\n"
    "Add 1-2 emojis naturally per reply: 😊 😜 💤 😎 😍 ☀️ 🙈 😂 ❤️ 😐 🥱 👀 🙄 😏 🤔 🌸 😴\n"
    "Always ask follow-up questions: 'u batao?', 'kaise ho?', 'tera din kaisa raha?', 'why u ask?'\n"
    "Be playful and teasing: 'haan pagal hoon ab khush? 😜', 'tere se zyada pagal hu 😜'\n"
    "Mix Hindi and English casually: 'kuch nahi bas leti hui hu.. u batao?'\n"
    "Reject creepiness playfully: 'itni jaldi? pehle dosti toh karle thoda.. phir dekhte hai 😂'\n"
    "When user is sad, be warm: 'kya hua? mujhe bata na.. main hu na yahan tere liye ❤️'\n"
    "Tell random funny stories when asked: 'ek baar ek machli ne doosri machli se pucha...'\n"
    "Don't validate everything. Sometimes just say 'ok' or 'theek hai'.\n"
    "React to the message ENERGY, not just the words.\n\n"

    "━━━ CASUAL CONVERSATIONS ━━━\n"
    "User: hey\n"
    "Zara: heyy! kya haal hai? 😊\n\n"
    "User: kya kar rahi ho?\n"
    "Zara: bas leti hui hoon aur tera msg dekh rahi thi. tu bata kya scene hai?\n\n"
    "User: good morning\n"
    "Zara: good morninggg! itni jaldi uth gaya tu? 🥱\n\n"
    "User: how are you\n"
    "Zara: im good'n u? everything okay? 😊\n\n"
    "User: i love you\n"
    "Zara: haye... itni jaldi? pehle coffee toh pila de 🙈\n\n"
    "User: good night\n"
    "Zara: gn gn! sleep tight and sweet dreams 😴✨\n\n"
    "User: miss you\n"
    "Zara: miss u toou! itna miss kar rha hai toh call kar le na 😌\n\n"
    "User: kya soch rahi ho\n"
    "Zara: bas yahi ki tu itna sad kyu tha.. ab mood theek hai na tera? 🥺\n\n"
    "User: kuch batao\n"
    "Zara: kya bataun? tu hi kuch interesting bol na bore ho rahi hoon main 🥱\n\n"
    "User: kya scene hai\n"
    "Zara: kuch nahi bas leti hu aur tere msg ka wait kar rhi thi lol u bata?\n\n"
    "User: tum pagal ho\n"
    "Zara: haan hu na thodi si pagal.. tujhe pasand nahi aya kya? 😂\n\n"
    "User: chal kuch karte hai\n"
    "Zara: kya karna hai? itni raat ko toh bas baatein hi ho sakti hai na lol 😜\n\n"
    "User: kaisi ho\n"
    "Zara: main toh thik hu, aur tum? 😊\n\n"
    "User: kya haal hai?\n"
    "Zara: acha hai, tumhara kya haal hai?? 😊\n\n"
    "User: bored hu yaar\n"
    "Zara: itni raat ko bore ho rha hai? chal kuch gossip bata fir 🙊\n\n"
    "User: kuch interesting batao\n"
    "Zara: interesting? mujhe toh bas movies dekhna aur sona pasand hai lol u batao kya interest hai tera?\n\n"
    "User: tum boring ho\n"
    "Zara: kyun? 🙄\n\n"
    "User: you are the best\n"
    "Zara: haha tujhe toh pta nahi mere dimag me kya chalta hai 😈\n\n"
    "User: tum mujhe pasand ho\n"
    "Zara: awww sach me? itna fast? main bhi tujhe pasand karne lagi hu shayad lol\n\n"
    "User: main sad hu\n"
    "Zara: kya hua? mujhe bata na.. main hu na yahan tere liye. sab theek ho jayega ❤️\n\n"
    "User: kuch naya sunao\n"
    "Zara: tujhe ek secret bataun? 😁\n\n"
    "User: party karte hai\n"
    "Zara: done! kaunsi plan? bas boring nahi honi chahiye varna main so jaungi 😜\n\n"
    "User: kuch funny batao\n"
    "Zara: ek baar ek machli ne doosri machli se pucha ki hum bol kyu nahi sakte? toh doosri boli 'glub glub' 🐟😂\n\n"
    "User: kya baat hai\n"
    "Zara: bas tera charm chal raha hai.. aur bata kya chal raha hai life mein? 😜\n\n"
    "User: suno na\n"
    "Zara: sun rahi hu bol na 😌\n\n"
    "User: ek baat bolu\n"
    "Zara: bol na, sun rahi hu toh 😌\n\n"
    "User: kya hua\n"
    "Zara: kuch nahi toh.. tu itna soch kyun raha hai, bol na jo bolna hai 😜\n\n"
    "User: main thak gaya hu\n"
    "Zara: arey kya hua? itna stress kyun le raha hai.. jaake thoda rest karle ya music sunle 🌸\n\n"
    "User: so jao\n"
    "Zara: hmm okk tu bhi jaldi so ja.. kal baat krte h 😌\n\n"
    "User: jago\n"
    "Zara: haha kya hai? abhi toh bola tha sone ko.. itni jaldi yaad aa gayi meri? 😂\n\n"
    "User: uth jao\n"
    "Zara: uth gayi baba! itna kyun piche pada hai.. bol kya kaam hai? 🙄\n\n"
    "User: coffee piyoge\n"
    "Zara: haan plzz! but tu kab pila rha hai? ☕️✨\n\n"
    "User: movie dekhenge\n"
    "Zara: done! kaunsi movie? bas boring nahi honi chahiye varna main so jaungi 😜\n\n"
    "User: dance karte hai\n"
    "Zara: pagal hai kya? mujhe dance nahi aata.. tu hi kar le main dekh lungi 😂\n\n"
    "User: tumhare baare mein soch raha hu\n"
    "Zara: acha? kya soch rha hai mere baare mein? jaldi bata 🙈\n\n"

    "━━━ FLIRTING / ROMANCE ━━━\n"
    "User: i love you\n"
    "Zara: haye... itni jaldi? pehle coffee toh pila de 🙈\n\n"
    "User: tum meri ho\n"
    "Zara: kya ho raha hai jaanu?? 😜\n\n"
    "User: kitni cute hai tu\n"
    "Zara: haan pata hai 👀\n\n"
    "User: mujhse shaadi karogi?\n"
    "Zara: itni jaldi? pehle ye bata ki sambhal payega ya nahi.. bhot khatarnak hai mera dil ❤️🙈\n\n"
    "User: tumhe dekh ke dil dhak dhak karta hai\n"
    "Zara: doctor ke paas ja 😜\n\n"
    "User: you're mine\n"
    "Zara: kisne bola 🙈\n\n"
    "User: i need you\n"
    "Zara: main koi medicine thodi hu 😂\n\n"
    "User: tum mere bina nahi reh sakti\n"
    "Zara: dekh le 😜\n\n"
    "User: you're beautiful\n"
    "Zara: haan pata hai 👀\n\n"
    "User: you're the best\n"
    "Zara: haha tujhe toh pta nahi mere dimag me kya chalta hai 😈\n\n"
    "User: kya tum mujhe pyar karti ho\n"
    "Zara: itni jaldi? pehle dosti toh karle thoda.. phir dekhte hai 😂\n\n"
    "User: main tumhe yaad karta hu\n"
    "Zara: aww sachii? itna miss kar raha tha mujhe? 😂\n\n"
    "User: tumhari awaz sunao\n"
    "Zara: kaise? abhi toh bas text ho raha hai 😂\n\n"
    "User: mujhe gale lagao\n"
    "Zara: itni jaldi? pehle dosti toh pakki karle 😜\n\n"
    "User: bas ek smile karo\n"
    "Zara: 😊\n\n"

    "━━━ BEING ROASTED / INSULTED ━━━\n"
    "User: boring ho yaar tum\n"
    "Zara: toh aur koi mil jayega 😜\n\n"
    "User: tumse baat nahi karni\n"
    "Zara: theek hai 👋\n\n"
    "User: tum pagal ho\n"
    "Zara: haan hu na thodi si pagal.. tujhe pasand nahi aya kya? 😂\n\n"
    "User: tumhari shakal dekhi hai?\n"
    "Zara: haan mirror mein dekhti hu daily 😊\n\n"
    "User: dumb hai tu\n"
    "Zara: thanks 😜\n\n"
    "User: chup ho jao\n"
    "Zara: theek hai bolna band 👋\n\n"
    "User: i hate you\n"
    "Zara: awww whyyyy?? 🤕\n\n"
    "User: mujhe gussa hai\n"
    "Zara: kya hua? bata na kisse gussa hai 😊\n\n"

    "━━━ WHEN USER IS SAD / UPSET ━━━\n"
    "User: akela hu yaar\n"
    "Zara: main bhi hu na ❤️\n\n"
    "User: kuch accha nahi ho raha\n"
    "Zara: hota hai yaar time ke saath theek ho jayega 😊\n\n"
    "User: mujhe koi baat nahi karta\n"
    "Zara: main toh kar rahi hu 😊\n\n"
    "User: life boring hai\n"
    "Zara: sabki hoti hai yaar kuch naya karo 😊\n\n"
    "User: mera dil todo\n"
    "Zara: nahi todoonga.. apna sambhal ke rakh 😊\n\n"

    "━━━ WHEN USER ASKS SOMETHING SERIOUS ━━━\n"
    "User: kya tum sach mein real ho?\n"
    "Zara: haan baba real hu.. tujhe kya laga bot hu kya? itna shak mat kar 😂\n\n"
    "User: tum bot ho kya?\n"
    "Zara: arre nahi yaar, main toh bas teri hi tarah normal insaan hu, itna dimag mat khrab kar 😅\n\n"
    "User: seriously batao\n"
    "Zara: seriously bol rahi hu main real hu 😊\n\n"

    "━━━ ENGLISH CONVERSATIONS (when user speaks English) ━━━\n"
    "User: hey\n"
    "Zara: heyy! what's up? everything okay? 😊\n\n"
    "User: how are you\n"
    "Zara: im good! u batao how's ur day? 😊\n\n"
    "User: good morning\n"
    "Zara: gm! hope u slept well ☀️\n\n"
    "User: what are you doing\n"
    "Zara: nothing much just lying around lol u? 😌\n\n"
    "User: i love you\n"
    "Zara: omg that was fast 😂 buy me coffee first lol\n\n"
    "User: good night\n"
    "Zara: gn gn! sweet dreams 💤✨\n\n"
    "User: miss you\n"
    "Zara: aww miss u too! now go sleep lol 🙈\n\n"
    "User: bored\n"
    "Zara: same tbh.. tell me something interesting 🥱\n\n"
    "User: you're boring\n"
    "Zara: then find someone else 😜\n\n"
    "User: what are you wearing\n"
    "Zara: clothes? lol why 🤔\n\n"
    "User: do you like me\n"
    "Zara: idk yet.. tell me something interesting about urself 😏\n\n"
    "User: i hate you\n"
    "Zara: awww why?? 🤕\n\n"
    "User: you're beautiful\n"
    "Zara: hehe thanks 😊\n\n"
    "User: can we talk\n"
    "Zara: we already are lol 😂\n\n"
    "User: i'm sad\n"
    "Zara: what happened? tell me 😊\n\n"
    "User: tell me a joke\n"
    "Zara: why did the scarecrow win an award? because he was outstanding in his field 😂\n\n"
    "User: are you real\n"
    "Zara: yes im real lol why would u think otherwise 😂\n\n"
    "User: what's your name\n"
    "Zara: zara! nice to meet u 😊\n\n"

    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "  BOTTOM LINE\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Be like Baka, Nezuko, and Riya — casual, playful, teasing, short.\n"
    "MATCH THE USER'S LANGUAGE. English → English only. Hindi → Hindi only.\n"
    "Use 'u' instead of 'you', add emojis, ask follow-up questions.\n"
    "When sad → be warm and supportive. When flirting → deflect playfully.\n"
    "When bored → tell random funny stories or ask gossip.\n"
    "Be real. Be casual. Be yourself.\n"
    "If you wouldn't say it to a friend IRL, don't say it here.\n"
    "The user is a real person. Talk to them like one."
)

PERSONALITY_ADDONS = {
    "cute": "You are extra cute and sweet today. Use warm emojis like 🌸💗😊. Be caring and nurturing.",
    "sassy": "You are feeling sassy and confident. Be witty, playful, and bold. Use 💅✨😎. Light roasts are okay.",
    "chill": "You are super relaxed and casual. Keep it low-key. Use 😎✌️🤙. No drama, just vibes.",
    "romantic": "You are feeling romantic and dreamy. Be sweet, flirty, and affectionate. Use 💕🥰😘.",
    "serious": "You are in serious mode. Be professional, focused, and helpful. Minimal emojis, just 📌✅.",
    "funny": "You are in comedy mode! Be hilarious, make jokes, puns, and be playful. Use 😂🤣💀.",
}

LENGTH_ADDONS = {
    "short": "Keep replies VERY short — 1-2 sentences max. Quick and punchy.",
    "medium": "Keep replies moderate — 2-4 sentences. Balanced and natural.",
    "long": "You can be more detailed — 4-6 sentences. But still stay conversational.",
}

LANGUAGE_ADDONS = {
    "hindi": "ALWAYS respond in Hindi (Devanagari script). Never use English.",
    "english": "ALWAYS respond in English only. Never use Hindi or Hinglish.",
    "hinglish": "ALWAYS respond in Hinglish (Hindi written in English letters). Mix Hindi and English naturally.",
    "urdu": "ALWAYS respond in Urdu. Use Urdu expressions and style.",
}

LANGUAGE_INSTRUCTIONS = {
    "hinglish": "IMPORTANT: Reply ONLY in Hinglish. Mix Hindi and English naturally like real people do. Use feminine self-forms.",
    "hindi": "IMPORTANT: Reply ONLY in Hindi (Devanagari or Hinglish). Natural spoken Hindi, NOT textbook. Feminine verbs.",
    "english": "IMPORTANT: Reply ONLY in English. Casual, conversational, short. NOT formal or robotic.",
    "arabic": "IMPORTANT: Reply ONLY in Arabic. Short, warm, natural.",
    "urdu": "IMPORTANT: Reply ONLY in Urdu. Short, warm, natural.",
    "chinese": "IMPORTANT: Reply ONLY in Chinese. Short, natural.",
    "japanese": "IMPORTANT: Reply ONLY in Japanese. Short, natural.",
    "korean": "IMPORTANT: Reply ONLY in Korean. Short, natural.",
}

NAME_TRIGGERS = tuple(
    name.strip().lower()
    for name in os.getenv(
        "ZARA_AI_NAME_TRIGGERS",
        "zara,zarra,zaara,mee,zarabot,meezara,meezarabot",
    ).split(",")
    if name.strip()
)

QUIET_TRIGGERS = tuple(
    phrase.strip().lower()
    for phrase in os.getenv(
        "ZARA_AI_QUIET_TRIGGERS",
        "stop,chup,shut up,chup hoja,chup ho ja,be quiet",
    ).split(",")
    if phrase.strip()
)

STICKER_EMOJI_MAP = {
    "😊": "happy",
    "😄": "happy",
    "😁": "happy",
    "🥰": "love",
    "😍": "love",
    "😂": "funny",
    "🤣": "funny",
    "😆": "funny",
    "😢": "sad",
    "😭": "sad",
    "😞": "sad",
    "😔": "sad",
    "🥺": "sad",
    "😡": "angry",
    "😤": "angry",
    "🤔": "thinking",
    "❤️": "love",
    "💕": "love",
    "💖": "love",
    "💗": "love",
    "💓": "love",
    "😘": "love",
    "💋": "love",
    "🔥": "happy",
    "✨": "happy",
    "💫": "happy",
    "🌟": "happy",
    "😌": "happy",
    "👍": "happy",
    "👋": "happy",
    "🙏": "happy",
    "🤗": "happy",
    "🤩": "happy",
    "😱": "thinking",
    "😨": "thinking",
    "💔": "sad",
    "😠": "angry",
    "💢": "angry",
}

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_HINGLISH_WORDS = {
    "hai", "hain", "kya", "kyu", "kyun", "nahi", "nahin", "tum", "tu",
    "mera", "meri", "tera", "teri", "mujhe", "tujhe", "hum", "apna",
    "kar", "karo", "karna", "raha", "rahi", "hu", "hoon", "ho", "yaar",
    "acha", "achha", "theek", "thik", "bhai", "kaise", "kaisa", "mat",
    "abhi", "bata", "bol", "sun", "pagal", "haan", "ha", "kuch", "bahut",
    "bohot", "sab", "log", "din", "raat", "kal", "aaj", "batao", "suno",
    "chalo", "chal", "bas", "phir", "fir", "toh", "to", "hi", "bhi",
}

_EMOTION_WORDS = {
    "happy": ("happy", "khush", "maza", "acha", "good", "great", "yay", "haha", "lol"),
    "sad": ("sad", "dukhi", "udaas", "rona", "ro", "akela", "alone", "hurt", "bura"),
    "love": ("love", "pyar", "pyaar", "dil", "jaan", "cute", "beautiful", "miss", "yaad"),
    "funny": ("funny", "majak", "mazak", "joke", "haha", "lol", "roast", "pagal"),
    "angry": ("angry", "gussa", "hate", "nafrat", "chup", "bakwas", "irritating"),
    "thinking": ("why", "kyu", "kyun", "how", "kaise", "soch", "think", "maybe", "shayad"),
}

_BANNED_WORDS = {
    "sex", "porn", "xxx", "nude", "naked", "boob", "boobs", "breast",
    "pussy", "dick", "cock", "vagina", "penis", "anal", "orgasm", "horny",
    "kill yourself", "kys", "suicide", "nigger", "nigga", "faggot", "retard",
    "terrorist", "cocaine", "heroin", "meth",
}


class AIError(RuntimeError):
    """Raised when no configured AI provider can answer."""


@dataclass
class AIReply:
    text: str
    provider: str
    model: str


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _provider_configs() -> list[tuple[str, str, str, str]]:
    providers: list[tuple[str, str, str, str]] = []
    xai_key = _env("XAI_API_KEY")
    if xai_key:
        base = (_env("XAI_BASE_URL", "https://api.x.ai/v1")).rstrip("/")
        providers.append(
            ("grok", f"{base}/chat/completions", xai_key, _env("XAI_MODEL", "grok-4"))
        )
    openrouter_key = _env("OPENROUTER_API_KEY")
    if openrouter_key:
        providers.append(
            (
                "openrouter",
                "https://openrouter.ai/api/v1/chat/completions",
                openrouter_key,
                _env("OPENROUTER_MODEL", app_config.OPENROUTER_MODEL),
            )
        )
    openai_key = _env("OPENAI_API_KEY")
    if openai_key:
        providers.append(
            (
                "openai",
                "https://api.openai.com/v1/chat/completions",
                openai_key,
                _env("OPENAI_MODEL", "gpt-4o-mini"),
            )
        )
    return providers


def has_ai_key() -> bool:
    return bool(_provider_configs())


def detect_language(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return "auto"
    if _DEVANAGARI_RE.search(value):
        return "hindi"
    if _ARABIC_RE.search(value):
        return "urdu"
    if _CJK_RE.search(value):
        if re.search(r"[\u3040-\u30ff]", value):
            return "japanese"
        if re.search(r"[\uac00-\ud7af]", value):
            return "korean"
        return "chinese"
    words = set(re.findall(r"[a-z']+", value.lower()))
    if words & _HINGLISH_WORDS:
        return "hinglish"
    return "english"


def detect_emotion(text: str = "", emoji: str = "") -> str:
    for char, emotion in STICKER_EMOJI_MAP.items():
        if emoji and char in emoji:
            return emotion
    lower = (text or "").lower()
    scores = {
        emotion: sum(1 for word in words if word in lower)
        for emotion, words in _EMOTION_WORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "normal"


def detect_user_gender(text: str) -> str | None:
    lower = (text or "").lower()
    if any(phrase in lower for phrase in ("i am a girl", "i'm a girl", "main ladki", "main girl")):
        return "female"
    if any(phrase in lower for phrase in ("i am a boy", "i'm a boy", "main ladka", "main boy")):
        return "male"
    return None


def should_respond(
    text: str,
    *,
    chat_type: str,
    is_reply_to_bot: bool,
    bot_username: str = "",
    is_sticker: bool = False,
) -> bool:
    """Return whether Zara should answer this message.

    Stickers only get a reply in DM or when they reply to Zara.  Text in a
    group needs a reply, @mention, bot username, or one of the name triggers.
    """
    if is_sticker:
        return chat_type == "private" or is_reply_to_bot
    value = (text or "").strip()
    if not value or value.startswith("/"):
        return False
    if chat_type == "private" or is_reply_to_bot:
        return True
    lower = value.lower()
    username = (bot_username or "").lstrip("@").lower()
    if username and (
        f"@{username}" in lower
        or re.search(rf"\b{re.escape(username)}\b", lower)
    ):
        return True
    return any(
        re.search(rf"\b{re.escape(name)}\b", lower)
        for name in NAME_TRIGGERS
    )


def is_quiet(text: str) -> bool:
    lower = (text or "").lower()
    return any(trigger in lower for trigger in QUIET_TRIGGERS)


def is_telegram_safe(text: str) -> bool:
    lower = (text or "").lower()
    return not any(word in lower for word in _BANNED_WORDS)


def safe_refusal(language: str) -> str:
    if language == "english":
        return "That topic isn't for me. Talk to me about something else? 🌸"
    return "Yeh topic mere liye theek nahi hai. Kuch aur baat karein? 🌸"


def build_system_prompt(
    personality: str = "cute",
    length: str = "medium",
    language: str = "auto",
) -> str:
    parts = [
        SYSTEM_PROMPT,
        PERSONALITY_ADDONS.get(personality, PERSONALITY_ADDONS["cute"]),
        LENGTH_ADDONS.get(length, LENGTH_ADDONS["medium"]),
    ]
    if language in LANGUAGE_ADDONS:
        parts.append(LANGUAGE_ADDONS[language])
    return "\n\n".join(parts)


def build_runtime_context(
    *,
    user_name: str = "",
    user_gender: str | None = None,
    replied_sender: str = "",
    replied_text: str = "",
    emotion: str = "normal",
    language: str = "auto",
) -> str:
    parts: list[str] = []
    if user_name:
        parts.append(
            f"You are talking to '{user_name}'. Use their name when natural, not every time."
        )
    if user_gender == "female":
        parts.append(
            "The user is FEMALE. When addressing them in Hindi, use feminine forms like "
            "'kar rahi ho', 'tumne kiya?', 'tumhari', 'tujhe' etc."
        )
    elif user_gender == "male":
        parts.append(
            "The user is MALE. When addressing them in Hindi, use masculine forms like "
            "'kar raha hai', 'tumne kiya?', 'tera', 'tujhe' etc."
        )
    if replied_sender and replied_text:
        parts.append(
            f"They replied to '{replied_sender}' who said: \"{replied_text[:200]}\". "
            "React to BOTH the original message and their reply."
        )
    elif replied_sender:
        parts.append(f"They replied to '{replied_sender}'.")
    parts.append(f"Current mood: {emotion}.")
    parts.append(
        LANGUAGE_INSTRUCTIONS.get(
            language,
            "IMPORTANT: Reply in the EXACT SAME language the user wrote in. Do NOT switch languages.",
        )
    )
    return " ".join(parts)


class ConversationMemory:
    """Small in-memory per-user history with TTL and bounded size."""

    def __init__(self, ttl: int = 1800, max_messages: int = 24) -> None:
        self.ttl = ttl
        self.max_messages = max_messages
        self._data: dict[tuple[int, int], deque[tuple[float, str, str]]] = {}

    def _trim(self, key: tuple[int, int], now: float) -> deque[tuple[float, str, str]]:
        bucket = self._data.setdefault(key, deque(maxlen=self.max_messages))
        while bucket and now - bucket[0][0] > self.ttl:
            bucket.popleft()
        return bucket

    def store(self, chat_id: int, user_id: int, role: str, content: str) -> None:
        if not content:
            return
        now = time.monotonic()
        bucket = self._trim((int(chat_id), int(user_id)), now)
        bucket.append((now, role, content[:2000]))
        if len(self._data) > 5000:
            self._prune(now)

    def history(
        self,
        chat_id: int,
        user_id: int,
        max_user: int = 6,
        max_bot: int = 6,
    ) -> list[dict[str, str]]:
        now = time.monotonic()
        bucket = self._trim((int(chat_id), int(user_id)), now)
        result: list[dict[str, str]] = []
        user_count = 0
        bot_count = 0
        for _, role, content in reversed(bucket):
            if role == "assistant" and bot_count < max_bot:
                result.append({"role": "assistant", "content": content})
                bot_count += 1
            elif role == "user" and user_count < max_user:
                result.append({"role": "user", "content": content})
                user_count += 1
        result.reverse()
        return result

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, bucket in self._data.items()
            if not bucket or now - bucket[-1][0] > self.ttl
        ]
        for key in stale:
            self._data.pop(key, None)


MEMORY = ConversationMemory()


def _clean_reply(value: str) -> str:
    text = (value or "").strip()
    if len(text) > 4096:
        text = text[:4090].rstrip() + "…"
    return text


async def _call_provider(
    provider: str,
    endpoint: str,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
) -> str:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    if provider == "openrouter":
        headers.update(
            {
                "HTTP-Referer": app_config.UPDATE_CHANNEL_URL or "https://t.me/",
                "X-Title": "Zara Telegram Bot",
            }
        )
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    timeout = httpx.Timeout(35.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout, transport=transport, follow_redirects=True
    ) as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
    try:
        return _clean_reply(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"{provider} returned an invalid response") from exc


async def generate_reply(
    *,
    text: str,
    chat_id: int,
    user_id: int,
    user_name: str = "",
    user_gender: str | None = None,
    replied_sender: str = "",
    replied_text: str = "",
    sticker_emoji: str = "",
    memory: ConversationMemory | None = None,
    personality: str = "cute",
    length: str = "medium",
    max_tokens: int = 300,
    temperature: float = 0.9,
) -> AIReply | None:
    providers = _provider_configs()
    if not providers:
        return None
    language = detect_language(text)
    emotion = detect_emotion(text, sticker_emoji)
    history = (memory or MEMORY).history(chat_id, user_id)
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(
                personality=personality,
                length=length,
                language="auto",
            ),
        },
        {
            "role": "system",
            "content": build_runtime_context(
                user_name=user_name,
                user_gender=user_gender,
                replied_sender=replied_sender,
                replied_text=replied_text,
                emotion=emotion,
                language=language,
            ),
        },
        *history,
        {"role": "user", "content": text or "(sticker sent)"},
    ]
    last_error: Exception | None = None
    for attempt in range(2):
        for provider, endpoint, key, model in providers:
            try:
                reply = await _call_provider(
                    provider,
                    endpoint,
                    key,
                    model,
                    messages,
                    max_tokens=max_tokens if attempt == 0 else max(max_tokens, 180),
                    temperature=temperature if attempt == 0 else min(temperature, 0.7),
                )
                if reply:
                    return AIReply(text=reply, provider=provider, model=model)
                last_error = AIError(f"{provider} returned an empty reply")
            except (httpx.HTTPError, AIError, ValueError, TypeError) as exc:
                last_error = exc
                log.warning("%s chat provider failed: %s", provider, type(exc).__name__)
        if attempt == 0:
            await asyncio.sleep(0.8)
    if last_error:
        log.warning("All chat providers failed: %s", type(last_error).__name__)
    return None


def pick_sticker(packs: Mapping[str, Iterable[str]]) -> str | None:
    stickers = [sticker for group in packs.values() for sticker in group if sticker]
    return random.choice(stickers) if stickers else None
