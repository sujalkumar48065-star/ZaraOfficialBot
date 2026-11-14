import io

import pytest
from PIL import Image

from zara import tools


def test_safe_calc_basic_math():
    assert tools.safe_calc("2 + 2 * 5") == 12
    assert tools.safe_calc("(10 / 4)") == 2.5
    assert tools.safe_calc("sqrt(81) + round(2.6)") == 12


def test_safe_calc_rejects_code():
    with pytest.raises(tools.ToolError):
        tools.safe_calc("__import__('os').system('id')")
    with pytest.raises(tools.ToolError):
        tools.safe_calc("2 ** 100000")


def test_parse_currency_args():
    assert tools.parse_currency_args(["100", "usd", "inr"]) == (100.0, "USD", "INR")
    assert tools.parse_currency_args(["usd", "to", "inr"]) == (1.0, "USD", "INR")
    assert tools.parse_currency_args(["12.5", "eur", "in", "gbp"]) == (12.5, "EUR", "GBP")


def test_parse_poll():
    question, options = tools.parse_poll("Best game? | Card | Bomb | Roulette")
    assert question == "Best game?"
    assert options == ["Card", "Bomb", "Roulette"]
    with pytest.raises(tools.ToolError):
        tools.parse_poll("Only question | one option")


def test_parse_tts_and_translate_args():
    assert tools.parse_tts_args(["hi", "kaise", "ho"]) == ("kaise ho", "hi")
    assert tools.parse_tts_args(["hello"], "replied text") == ("hello", "en")
    assert tools.parse_tts_args([], "replied text") == ("replied text", "en")
    assert tools.parse_translate_args(["hi", "hello"]) == ("hi", "hello")
    assert tools.parse_translate_args(["hi"], "hello") == ("hi", "hello")
    assert tools.parse_translate_args([], "hello") == ("en", "hello")


def test_image_format_and_conversion():
    source = io.BytesIO()
    Image.new("RGBA", (20, 20), (255, 0, 0, 128)).save(source, format="PNG")
    converted, filename, label = tools.convert_image(source.getvalue(), "jpg")
    assert filename == "zara-image.jpg"
    assert label == "JPG"
    result = Image.open(io.BytesIO(converted))
    assert result.format == "JPEG"
    assert result.mode == "RGB"


def test_image_prompt_safety_and_weather_labels():
    assert tools.is_safe_image_prompt("cute cat in space")
    assert not tools.is_safe_image_prompt("nsfw image")
    assert tools.weather_label(0)[1] == "Clear sky"
    assert tools.weather_label(95)[0] == "⛈️"
