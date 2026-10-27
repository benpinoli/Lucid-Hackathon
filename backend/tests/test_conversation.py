"""The tutor's conversational wording: varied, short, never repeating itself."""
import re

from backend.app.conversation import (MAX_GLUE_WORDS, PHRASES, Phrasing, echo, glue, joined,
                                      options_redundant)


def test_every_phrase_is_short_and_formats():
    values = {"topic": "Derivatives", "known": "Slope", "answer": "2x", "options": "a, b, or c", "echo": ", 2x"}
    for key, variants in PHRASES.items():
        assert variants, key
        for variant in variants:
            text = variant.format(**values)
            assert len(text.split()) <= MAX_GLUE_WORDS, (key, text)
            assert not re.search(r"\b(mastery|continue button|choose continue|active recall|route|score)\b", text, re.I), text


def test_phrasing_varies_without_repeating_and_can_be_fixed():
    phrasing, last = Phrasing(seed=3), {}
    said = [phrasing.say("correct", last, echo="") for _ in range(30)]
    assert len(set(said)) > 1
    assert all(a != b for a, b in zip(said, said[1:]))  # never the same reaction twice in a row
    fixed = Phrasing(variety=False)
    assert {fixed.say("ready", {}) for _ in range(5)} == {PHRASES["ready"][0]}


def test_turn_glue_is_capped_and_deduplicated():
    assert glue(["Yes.", "", "Yes.", "Next up: Slope."]) == ["Yes.", "Next up: Slope."]
    assert glue(["Close.", "Try once more.", "Now, Slope."]) == ["Close.", "Now, Slope."]


def test_short_answers_are_echoed_long_ones_are_not():
    assert echo("2x", "2x") == ", 2x"
    assert echo("um it's 2x", "2x") == ", 2x"
    assert echo("I think it is the slope of the tangent line", "slope") == ""


def test_spoken_options_skip_redundant_and_yes_no_choices():
    assert options_redundant("Is the hypotenuse longer or shorter than either leg?", ["longer", "shorter"])
    assert options_redundant("Can a route with more edges cost less?", ["yes", "no"])
    assert not options_redundant("What is the derivative of x²?", ["2x", "x", "x³/3", "2"])
    assert joined(["2x", "x", "2"]) == "2x, x, or 2"
    assert joined(["zero", "its full length"]) == "zero or its full length"
