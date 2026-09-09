"""Regression tests for the Responses ``input`` normalizer.

The OpenAI Responses API ``input`` field is polymorphic: a plain string, or a
list of input items (the shape the Responses API, the eval harness, and the
Agent Optimizer all send). ``server._responses_input`` flattens both to the last
user turn's text. This is the fidelity bug that crashed the optimizer's eval
stage -- the handler assumed a string and did ``text.strip()`` on a list, so
every candidate scored ``None`` and the run failed with
``AllEvaluatorsFailedError``. These cases lock the normalizer so no future edit
silently reintroduces the string-only assumption.
"""

from __future__ import annotations

from castia.server import _responses_input


def test_plain_string_passthrough() -> None:
    assert _responses_input("hello world") == "hello world"


def test_empty_string() -> None:
    assert _responses_input("") == ""


def test_none_is_empty() -> None:
    assert _responses_input(None) == ""


def test_non_list_non_string_is_empty() -> None:
    assert _responses_input({"role": "user"}) == ""
    assert _responses_input(42) == ""


def test_list_role_string_content() -> None:
    value = [{"role": "user", "content": "say hi"}]
    assert _responses_input(value) == "say hi"


def test_list_content_parts() -> None:
    value = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "part one "},
                {"type": "input_text", "text": "part two"},
            ],
        }
    ]
    assert _responses_input(value) == "part one part two"


def test_output_text_and_text_part_types() -> None:
    value = [
        {"role": "user", "content": [{"type": "output_text", "text": "a"}]},
    ]
    assert _responses_input(value) == "a"
    value = [
        {"role": "user", "content": [{"type": "text", "text": "b"}]},
    ]
    assert _responses_input(value) == "b"


def test_prefers_last_user_turn() -> None:
    value = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]
    assert _responses_input(value) == "second"


def test_roleless_item_falls_back() -> None:
    value = [{"content": "no role here"}]
    assert _responses_input(value) == "no role here"


def test_user_turn_wins_over_later_roleless_without_text() -> None:
    # A trailing item with no extractable text must not shadow a real user turn.
    value = [
        {"role": "user", "content": "the question"},
        {"role": "assistant", "content": ""},
    ]
    assert _responses_input(value) == "the question"


def test_bare_string_items_fall_back_to_last() -> None:
    assert _responses_input(["first", "second"]) == "second"


def test_empty_list_is_empty() -> None:
    assert _responses_input([]) == ""
