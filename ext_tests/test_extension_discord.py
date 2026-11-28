"""The Discord bridge, driven with stand-ins for discord.py's objects.

discord.py itself (the gateway, the HTTP client) is the library's to test; what is the
extension's is what it makes of a message and of the Core's replies. The real process is
checked separately (``test_the_extension_loads_in_its_sandbox``).
"""

from __future__ import annotations

import asyncio
import datetime
import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "extensions" / "discord"
import discord  # noqa: E402  (the Core's environment has it; the package bundles its own)


def _load() -> Any:
    """The extension's ``ext_main`` (and its ``convert``) by their plain names, without
    leaving them in sys.modules: every extension has both."""
    sys.path.insert(0, str(SOURCE))
    try:
        return importlib.import_module("ext_main")
    finally:
        sys.path.remove(str(SOURCE))
        for name in ("convert", "ext_main", "calls", "voice"):
            sys.modules.pop(name, None)


ext_main = _load()

BOT = SimpleNamespace(id=42, name="Rei", display_name="Rei", bot=True)
ALICE = SimpleNamespace(id=7, name="alice", display_name="Alice", bot=False)
WHEN = datetime.datetime(2026, 11, 28, 10, 0, tzinfo=datetime.timezone.utc)


class Attachment:
    def __init__(self, filename: str, content_type: str, data: bytes = b"data") -> None:
        self.filename, self.content_type, self.data = filename, content_type, data
        self.size = len(data)

    async def read(self) -> bytes:
        return self.data


class Channel:
    """A text channel, a thread or a DM channel: it records what is sent to it."""

    def __init__(
        self, id: int, name: str | None = "general", guild: Any = None
    ) -> None:
        self.id, self.name, self.guild = id, name, guild
        self.sent: list[tuple[str | None, Any]] = []
        self.messages: dict[int, Any] = {}

    async def send(self, content: str | None = None, file: Any = None) -> None:
        self.sent.append((content, file))

    async def fetch_message(self, message_id: int) -> Any:
        if message_id not in self.messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "missing")
        return self.messages[message_id]


class Message:
    def __init__(
        self,
        id: int,
        author: Any,
        content: str,
        channel: Channel,
        guild: Any = None,
        *,
        reference: Any = None,
        attachments: list[Attachment] | None = None,
        roles: list[Any] | None = None,
    ) -> None:
        self.id, self.author, self.content = id, author, content
        self.channel, self.guild, self.reference = channel, guild, reference
        self.attachments = attachments or []
        self.role_mentions = roles or []
        self.created_at = WHEN
        self.replies: list[tuple[str | None, Any]] = []

    async def reply(self, content: str | None = None, file: Any = None) -> None:
        self.replies.append((content, file))

    async def add_reaction(self, emoji: str) -> None:
        self.reacted = emoji


class Client:
    def __init__(self) -> None:
        self.user = BOT
        self.channels: dict[int, Channel] = {}
        self.users: dict[int, Any] = {}
        self.typing: list[int] = []
        self.closed = False
        self.http = SimpleNamespace(send_typing=self._typing)

    async def _typing(self, channel_id: int) -> None:
        self.typing.append(channel_id)

    def get_channel(self, number: int) -> Channel | None:
        return self.channels.get(number)

    async def fetch_channel(self, number: int) -> Channel:
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "missing")

    def get_user(self, number: int) -> Any:
        return self.users.get(number)

    async def fetch_user(self, number: int) -> Any:
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "missing")

    def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


class Ctx:
    def __init__(self, tmp: Path, **config: Any) -> None:
        self.inbound: list[dict[str, Any]] = []
        self.registered: list[dict[str, Any]] = []
        self.config_values = config
        self._tmp = tmp
        self.stats = SimpleNamespace(incr=lambda *a, **k: None)
        self.log = SimpleNamespace(
            info=lambda m: None, warning=lambda m: None, error=lambda m: None
        )
        self.config = SimpleNamespace(get=self._get)
        self.interfaces = SimpleNamespace(
            inbound=self._inbound, register=self._register
        )
        self.provided: list[str] = []
        self.calls = SimpleNamespace(provider=self._provider)

    async def _get(self, key: str) -> Any:
        return self.config_values.get(key)

    async def _inbound(self, interface_id: str, path: str, **kw: Any) -> dict[str, Any]:
        self.inbound.append({"interface_id": interface_id, "path": path, **kw})
        return {"queued": True}

    async def _provider(self, interface_id: str) -> None:
        self.provided.append(interface_id)

    async def _register(self, interface_id: str, **kw: Any) -> dict[str, Any]:
        self.registered.append({"interface_id": interface_id, **kw})
        return {}

    def inbox_path(self, name: str = "file") -> Path:
        return self._tmp / f"in-{name}"


@pytest.fixture
def rig(tmp_path: Path) -> SimpleNamespace:
    client = Client()
    ctx = Ctx(tmp_path)
    return SimpleNamespace(
        ctx=ctx, client=client, bridge=ext_main.Bridge(ctx, client), tmp=tmp_path
    )


GUILD = SimpleNamespace(id=11, name="Home")


class TestInbound:
    @pytest.mark.asyncio
    async def test_a_mention_in_a_server_channel_is_addressed(
        self, rig: SimpleNamespace
    ) -> None:
        channel = Channel(22, "general", GUILD)
        await rig.bridge.on_message(
            Message(100, ALICE, "<@42> hello there", channel, GUILD)
        )
        [call] = rig.ctx.inbound
        assert call["path"] == "discord_bot/11/22"
        assert call["text"] == "@Rei hello there"
        assert call["is_explicit_trigger"] is True
        assert (call["chat_type"], call["chat_id"], call["thread_id"]) == (
            "group",
            "22",
            None,
        )
        assert call["sender_id"] == "7" and call["sender_name"] == "Alice"
        assert call["message_id"] == "100" and call["timestamp"] == WHEN.isoformat()

    @pytest.mark.asyncio
    async def test_a_plain_message_in_a_server_is_not_addressed(
        self, rig: SimpleNamespace
    ) -> None:
        await rig.bridge.on_message(
            Message(101, ALICE, "just chatting", Channel(22, "general", GUILD), GUILD)
        )
        assert rig.ctx.inbound[0]["is_explicit_trigger"] is False

    @pytest.mark.asyncio
    async def test_a_direct_message_is_private_and_addressed(
        self, rig: SimpleNamespace
    ) -> None:
        await rig.bridge.on_message(Message(102, ALICE, "hi", Channel(900, None), None))
        [call] = rig.ctx.inbound
        assert call["path"] == "discord_bot/7"
        assert call["chat_type"] == "private" and call["is_explicit_trigger"] is True

    @pytest.mark.asyncio
    async def test_a_thread_repeats_its_id_in_the_path(
        self, rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeThread(Channel):
            pass

        monkeypatch.setattr(discord, "Thread", FakeThread)
        await rig.bridge.on_message(
            Message(103, ALICE, "in a thread", FakeThread(33, "idea", GUILD), GUILD)
        )
        [call] = rig.ctx.inbound
        assert call["path"] == "discord_bot/11/33/33"
        assert call["thread_id"] == "33" and call["chat_id"] == "33"

    @pytest.mark.asyncio
    async def test_a_reply_to_the_bot_is_addressed_and_quotes_it(
        self, rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(discord, "Message", Message)
        channel = Channel(22, "general", GUILD)
        mine = Message(50, BOT, "earlier answer", channel, GUILD)
        ref = SimpleNamespace(resolved=mine, message_id=50)
        await rig.bridge.on_message(
            Message(104, ALICE, "and then?", channel, GUILD, reference=ref)
        )
        [call] = rig.ctx.inbound
        assert call["is_explicit_trigger"] is True
        assert call["reply_to"] == {
            "sender_name": "Rei",
            "text": "earlier answer",
            "message_id": "50",
            "sender_id": "42",
        }

    @pytest.mark.asyncio
    async def test_roles_are_spelled_by_name(self, rig: SimpleNamespace) -> None:
        await rig.bridge.on_message(
            Message(
                105,
                ALICE,
                "<@&9> assemble",
                Channel(22, "g", GUILD),
                GUILD,
                roles=[SimpleNamespace(id=9, name="mods")],
            )
        )
        assert rig.ctx.inbound[0]["text"] == "@mods assemble"

    @pytest.mark.asyncio
    async def test_its_own_and_other_bots_messages_are_ignored(
        self, rig: SimpleNamespace
    ) -> None:
        channel = Channel(22, "g", GUILD)
        other_bot = SimpleNamespace(id=8, name="b", display_name="B", bot=True)
        await rig.bridge.on_message(Message(1, BOT, "echo", channel, GUILD))
        await rig.bridge.on_message(Message(2, other_bot, "beep", channel, GUILD))
        assert rig.ctx.inbound == []

    @pytest.mark.asyncio
    async def test_ping_is_answered_here_and_not_sent_to_the_core(
        self, rig: SimpleNamespace
    ) -> None:
        channel = Channel(22, "g", GUILD)
        await rig.bridge.on_message(Message(3, ALICE, "<@42> ping", channel, GUILD))
        assert channel.sent == [("pong", None)] and rig.ctx.inbound == []

    @pytest.mark.asyncio
    async def test_a_voice_message_is_voice_input_and_the_file_is_removed_after(
        self, rig: SimpleNamespace
    ) -> None:
        seen: dict[str, Any] = {}

        async def inbound(interface_id: str, path: str, **kw: Any) -> dict[str, Any]:
            for item in kw["attachments"]:
                seen[item["filename"]] = Path(item["path"]).read_bytes()
            rig.ctx.inbound.append(kw)
            return {}

        rig.ctx.interfaces.inbound = inbound
        await rig.bridge.on_message(
            Message(
                106,
                ALICE,
                "",
                Channel(900, None),
                None,
                attachments=[Attachment("voice-message.ogg", "audio/ogg", b"OGG!")],
            )
        )
        [call] = rig.ctx.inbound
        assert call["is_voice_input"] is True and call["skip_mention_check"] is True
        assert seen == {"voice-message.ogg": b"OGG!"}
        assert not list(rig.tmp.glob("in-*"))  # nothing left behind

    @pytest.mark.asyncio
    async def test_an_oversized_attachment_is_not_downloaded(
        self, rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ext_main.convert, "MAX_DOWNLOAD", 3)
        await rig.bridge.on_message(
            Message(
                107,
                ALICE,
                "big",
                Channel(900, None),
                None,
                attachments=[Attachment("x.bin", "application/octet-stream", b"12345")],
            )
        )
        assert rig.ctx.inbound[0]["attachments"] is None

    @pytest.mark.asyncio
    async def test_a_failing_core_call_does_not_stop_the_client(
        self, rig: SimpleNamespace
    ) -> None:
        async def boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("core down")

        rig.ctx.interfaces.inbound = boom
        await rig.bridge.on_message(Message(108, ALICE, "hi", Channel(900, None), None))


class TestOutbound:
    def prepare(self, rig: SimpleNamespace) -> Channel:
        channel = Channel(22, "general", GUILD)
        rig.client.channels[22] = channel
        return channel

    @pytest.mark.asyncio
    async def test_text_goes_to_the_channel_of_the_path_in_chunks(
        self, rig: SimpleNamespace
    ) -> None:
        channel = self.prepare(rig)
        text = ("a" * 1500) + "\n\n" + ("b" * 1500)
        result = await rig.bridge.send(
            "discord_bot", {"text": text, "interface_path": "discord_bot/11/22"}, {}
        )
        assert result == {"ok": True}
        assert [c for c, _ in channel.sent] == ["a" * 1500, "b" * 1500]

    @pytest.mark.asyncio
    async def test_it_answers_where_the_message_came_from_when_no_path_is_given(
        self, rig: SimpleNamespace
    ) -> None:
        channel = self.prepare(rig)
        await rig.bridge.send(
            "discord_bot", {"text": "hi"}, {"interface_path": "discord_bot/11/22"}
        )
        assert channel.sent == [("hi", None)]

    @pytest.mark.asyncio
    async def test_a_reply_quotes_the_message_and_a_missing_one_is_sent_plain(
        self, rig: SimpleNamespace
    ) -> None:
        channel = self.prepare(rig)
        quoted = Message(500, ALICE, "q", channel, GUILD)
        channel.messages[500] = quoted
        path = "discord_bot/11/22"
        await rig.bridge.send(
            "discord_bot", {"text": "yes", "interface_path": path, "reply_to": 500}, {}
        )
        assert quoted.replies == [("yes", None)] and channel.sent == []
        await rig.bridge.send(
            "discord_bot", {"text": "no", "interface_path": path, "reply_to": 999}, {}
        )
        assert channel.sent == [("no", None)]

    @pytest.mark.asyncio
    async def test_files_are_sent_with_the_text_as_caption_once(
        self, rig: SimpleNamespace
    ) -> None:
        channel = self.prepare(rig)
        one, two = rig.tmp / "one.png", rig.tmp / "two.png"
        one.write_bytes(b"1")
        two.write_bytes(b"2")
        result = await rig.bridge.send(
            "discord_bot",
            {
                "text": "look",
                "interface_path": "discord_bot/11/22",
                "attachments": [
                    {"path": str(one), "name": "one.png"},
                    {"path": str(two), "name": "two.png"},
                ],
            },
            {},
        )
        assert result == {"ok": True}
        assert [(c, f.filename) for c, f in channel.sent] == [
            ("look", "one.png"),
            (None, "two.png"),
        ]

    @pytest.mark.asyncio
    async def test_a_user_id_is_a_direct_message(self, rig: SimpleNamespace) -> None:
        dm = Channel(900, None)
        rig.client.users[7] = dm  # a user: ``send`` is all that is used
        result = await rig.bridge.send(
            "discord_bot", {"text": "psst", "interface_path": "discord_bot/7"}, {}
        )
        assert result == {"ok": True} and dm.sent == [("psst", None)]

    @pytest.mark.parametrize(
        "payload",
        [
            {"text": "x"},  # no path, no origin
            {"text": "x", "interface_path": "discord_bot/11/999"},  # nobody there
            {"text": "", "interface_path": "discord_bot/11/22"},  # nothing to send
        ],
    )
    @pytest.mark.asyncio
    async def test_what_cannot_be_delivered_says_so(
        self, rig: SimpleNamespace, payload: dict[str, Any]
    ) -> None:
        self.prepare(rig)
        result = await rig.bridge.send("discord_bot", payload, {})
        assert result["ok"] is False and result["error"]

    @pytest.mark.asyncio
    async def test_a_discord_error_is_one_failed_delivery_not_a_crash(
        self, rig: SimpleNamespace
    ) -> None:
        channel = self.prepare(rig)

        async def refuse(*a: Any, **k: Any) -> None:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")

        channel.send = refuse  # type: ignore[method-assign]
        result = await rig.bridge.send(
            "discord_bot", {"text": "hi", "interface_path": "discord_bot/11/22"}, {}
        )
        assert result["ok"] is False and "Forbidden" in result["error"]

    @pytest.mark.asyncio
    async def test_typing_reaction_and_names(self, rig: SimpleNamespace) -> None:
        channel = self.prepare(rig)
        message = Message(600, ALICE, "x", channel, GUILD)
        channel.messages[600] = message
        await rig.bridge.activity("discord_bot", "discord_bot/11/22", "typing")
        assert rig.client.typing == [22]
        assert await rig.bridge.react("discord_bot", "22", "600", "👍") is True
        assert message.reacted == "👍"
        assert await rig.bridge.react("discord_bot", "22", "601", "👍") is False
        assert await rig.bridge.resolve_names("discord_bot", "22", None) == {
            "chat_name": "Home · #general"
        }
        assert await rig.bridge.resolve_names("discord_bot", "999", None) == {
            "chat_name": "999"
        }


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_it_registers_once_with_its_identity(
        self, rig: SimpleNamespace
    ) -> None:
        await rig.bridge.on_ready()
        await rig.bridge.on_ready()  # a reconnect
        [reg] = rig.ctx.registered
        assert reg["interface_id"] == "discord_bot" and reg["bot_id"] == "42"
        assert {"send_file", "activity", "reaction", "avatar", "call"} <= set(
            reg["capabilities"]
        )
        assert set(reg["actions"]) == {"join_voice_discord", "leave_voice_discord"}
        assert rig.ctx.provided == ["discord_bot"]  # it hosts calls

    @pytest.mark.asyncio
    async def test_when_the_core_refuses_the_interface_it_disconnects(
        self, rig: SimpleNamespace
    ) -> None:
        async def refuse(interface_id: str, **kw: Any) -> None:
            raise RuntimeError("interface id 'discord_bot' is already registered")

        rig.ctx.interfaces.register = refuse
        await rig.bridge.on_ready()
        assert rig.client.closed is True and rig.bridge.registered is False
        assert "already registered" in str(rig.bridge.refusal)

    @pytest.mark.parametrize(
        ("problem", "words"),
        [
            (discord.LoginFailure("bad"), "refused the token"),
            (discord.PrivilegedIntentsRequired(None), "Message Content"),
            (OSError("down"), "could not connect"),
        ],
    )
    def test_a_refusal_is_explained(self, problem: Exception, words: str) -> None:
        assert words in ext_main.refusal_reason(problem)

    @pytest.mark.asyncio
    async def test_without_a_token_it_idles(self, tmp_path: Path) -> None:
        ctx = Ctx(tmp_path, DISCORD_BOT_TOKEN="")
        ctx.stopping = asyncio.Event()  # type: ignore[attr-defined]
        task = asyncio.ensure_future(ext_main.main(ctx))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        assert not task.done()
        ctx.stopping.set()
        await asyncio.wait_for(task, 2)

    @pytest.mark.asyncio
    async def test_the_avatar_follows_its_setting(self, rig: SimpleNamespace) -> None:
        rig.ctx.config_values["DISCORD_BOT_USE_SYNTH_AVATAR"] = False
        assert await rig.bridge.set_avatar("discord_bot", b"png", "v1", False) is False


def test_the_extension_loads_in_its_sandbox(tmp_path: Path) -> None:
    """``python -S -I`` with the package's own ``lib/``: discord.py imports there."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build the extension's lib/")
    lib = tmp_path / "lib"
    from core.extensions.manifest import load_manifest

    requirements = load_manifest(SOURCE / "synth-extension.toml").python_requirements
    done = subprocess.run(
        [uv, "pip", "install", "--offline", "--target", str(lib), *requirements],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        pytest.skip(f"cannot build lib/ offline: {done.stderr.strip()[-200:]}")
    core = Path(__file__).resolve().parents[2] / "Synthetic_Heart"
    code = (
        "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2], sys.argv[3]]; "
        "import convert, voice, calls, ext_main, discord; "
        "print(discord.__version__, voice.HAS_VOICE_RECV, voice.apply_patches())"
    )
    run = subprocess.run(
        [sys.executable, "-S", "-I", "-c", code, str(lib), str(SOURCE), str(core)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr[-400:]
    version, has_recv, patched = run.stdout.split()
    assert version[0].isdigit() and has_recv == "True" and patched == "True"
