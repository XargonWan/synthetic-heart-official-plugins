"""The pure helpers of the Discord extension."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent


def _load():  # type: ignore[no-untyped-def]
    """The extension's ``convert`` by its plain name, without leaving it in sys.modules
    (every extension has one)."""
    sys.path.insert(0, str(SOURCE))
    try:
        return importlib.import_module("convert")
    finally:
        sys.path.remove(str(SOURCE))
        sys.modules.pop("convert", None)


convert = _load()


class TestPaths:
    def test_a_server_channel_a_thread_and_a_direct_message(self) -> None:
        assert convert.build_path(11, 22) == "discord_bot/11/22"
        # a thread is a channel of its own: its id is repeated (how its history is stored)
        assert convert.build_path(11, 33, 33) == "discord_bot/11/33/33"
        assert convert.build_path(None, 44, user_id=55) == "discord_bot/55"

    def test_the_destination_is_the_last_level(self) -> None:
        assert convert.destination_of("discord_bot/11/22") == "22"
        assert convert.destination_of("discord_bot/11/33/33") == "33"
        assert convert.destination_of("discord_bot/55") == "55"

    @pytest.mark.parametrize(
        "path",
        [
            None,
            "",
            "telegram_bot/1/2",
            "discord_bot",
            "discord_bot/x/2",
            "discord_bot/1/2/3/4",
        ],
    )
    def test_anything_else_has_no_destination(self, path: str | None) -> None:
        assert convert.destination_of(path) is None

    def test_a_direct_message_is_one_level(self) -> None:
        assert convert.is_direct("discord_bot/55") is True
        assert convert.is_direct("discord_bot/11/22") is False
        assert convert.is_direct("nonsense") is False


class TestReplyId:
    def test_a_numeric_id_that_is_not_the_channel(self) -> None:
        assert convert.usable_reply_id("987", "22") == 987
        assert convert.usable_reply_id(987, "22") == 987

    @pytest.mark.parametrize("value", [None, "", "abc", "22"])
    def test_anything_else_is_not_quoted(self, value: object) -> None:
        assert convert.usable_reply_id(value, "22") is None


class TestSplitText:
    def test_short_text_is_one_chunk_and_empty_is_none(self) -> None:
        assert convert.split_text("hello") == ["hello"]
        assert convert.split_text("   ") == []
        assert convert.split_text(None) == []

    def test_long_text_is_cut_at_a_paragraph_then_a_line(self) -> None:
        text = ("a" * 1500) + "\n\n" + ("b" * 1500)
        chunks = convert.split_text(text)
        assert chunks == ["a" * 1500, "b" * 1500]
        assert all(len(c) <= convert.MESSAGE_LIMIT for c in chunks)

    def test_a_run_with_no_boundary_is_cut_hard(self) -> None:
        chunks = convert.split_text("x" * 4500)
        assert [len(c) for c in chunks] == [2000, 2000, 500]
        assert "".join(chunks) == "x" * 4500

    def test_nothing_is_lost(self) -> None:
        text = " ".join(f"sentence {i}." for i in range(600))
        assert " ".join(convert.split_text(text)).replace("  ", " ") == text


class TestMentions:
    def test_the_bots_mention_becomes_its_name_and_counts(self) -> None:
        content, mentioned = convert.spell_mentions(
            "<@42> hello <@!42>", bot_id=42, bot_name="Rei"
        )
        assert content == "@Rei hello @Rei" and mentioned is True

    def test_someone_elses_mention_is_left_and_does_not_count(self) -> None:
        content, mentioned = convert.spell_mentions(
            "<@7> hi", bot_id=42, bot_name="Rei"
        )
        assert content == "<@7> hi" and mentioned is False

    def test_a_role_mention_is_its_name(self) -> None:
        content, _ = convert.spell_mentions(
            "<@&9> assemble", bot_id=42, bot_name="Rei", roles={"9": "mods"}
        )
        assert content == "@mods assemble"

    def test_ping_with_or_without_the_mention(self) -> None:
        assert convert.is_ping("ping", "Rei") and convert.is_ping("@Rei  Ping", "Rei")
        assert not convert.is_ping("ping pong", "Rei")


class TestTriggers:
    @pytest.mark.parametrize(
        ("direct", "mentioned", "replied", "expected"),
        [
            (True, False, False, True),
            (False, True, False, True),
            (False, False, True, True),
            (False, False, False, False),
        ],
    )
    def test_addressed_means_dm_mention_or_reply(
        self, direct: bool, mentioned: bool, replied: bool, expected: bool
    ) -> None:
        assert (
            convert.is_explicit(
                direct=direct, mentioned=mentioned, replied_to_bot=replied
            )
            is expected
        )


class TestMisc:
    def test_the_reply_block_has_what_the_core_takes(self) -> None:
        block = convert.reply_block(
            message_id=5, text="hi", author_id=6, author_name=None
        )
        assert block == {
            "sender_name": "Unknown",
            "text": "hi",
            "message_id": "5",
            "sender_id": "6",
        }

    def test_audio_is_found_by_type_or_by_name(self) -> None:
        assert convert.audio_kind("audio/ogg", "voice-message.ogg")
        assert convert.audio_kind(None, "song.MP3")
        assert not convert.audio_kind("image/png", "x.png")
        assert not convert.audio_kind(None, None)
