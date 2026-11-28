"""Discord bot interface extension.

Talks to Discord with discord.py (bundled in ``lib/``) and to the Core only through
``ctx``. It imports nothing from the Core.

Flow: a Discord message -> :meth:`Bridge.on_message` -> ``ctx.interfaces.inbound``; the
Core calls back into :meth:`Bridge.send` (and the activity, reaction, name and avatar
handlers) to act on Discord. The Core owns history, commands, attention and the queue.

What this extension does not carry over from the in-tree interface it replaces: voice
channels, calls and the Live voice session (they need the calls and duplex-audio APIs and
come in a later step).
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import convert
import discord

from synth_sdk.runtime import Context


class Bridge:
    """One bot account, one process."""

    def __init__(self, ctx: Context, client: discord.Client) -> None:
        self.ctx = ctx
        self.client = client
        self.registered = False

    # ------------------------------------------------------------- inbound

    async def on_message(self, message: discord.Message) -> None:
        me = self.client.user
        if me is None or message.author.id == me.id:
            return  # our own message
        if message.author.bot:
            return  # another bot: no loops
        try:
            await self._deliver_inbound(message, me)
        except Exception as exc:  # noqa: BLE001 - one message must not stop the client
            self.ctx.stats.incr("errors", kind="inbound")
            self.ctx.log.error(f"inbound {message.id}: {type(exc).__name__}: {exc}")

    async def _deliver_inbound(self, message: discord.Message, me: Any) -> None:
        channel = message.channel
        guild = message.guild
        thread_id = channel.id if isinstance(channel, discord.Thread) else None
        roles = {str(r.id): r.name for r in getattr(message, "role_mentions", [])}
        content, mentioned = convert.spell_mentions(
            (message.content or "").strip(),
            bot_id=me.id,
            bot_name=me.name,
            roles=roles,
        )
        if convert.is_ping(content, me.name):
            await channel.send("pong")
            return
        path = convert.build_path(
            guild.id if guild is not None else None,
            channel.id,
            thread_id,
            message.author.id,
        )
        replied = await self._replied_message(message)
        replied_to_bot = replied is not None and replied.author.id == me.id
        files: list[Path] = []
        attachments: list[dict[str, Any]] = []
        voice = False
        try:
            for attachment in message.attachments:
                if attachment.size > convert.MAX_DOWNLOAD:
                    continue
                target = self.ctx.inbox_path(attachment.filename)
                target.write_bytes(await attachment.read())
                files.append(target)
                mime = attachment.content_type or "application/octet-stream"
                attachments.append(
                    {
                        "path": str(target),
                        "filename": attachment.filename,
                        "mime_type": mime,
                    }
                )
                voice = voice or convert.audio_kind(mime, attachment.filename)
            author = message.author
            await self.ctx.interfaces.inbound(
                convert.INTERFACE_ID,
                path,
                text=content,
                sender_id=str(author.id),
                sender_name=author.display_name or author.name,
                sender_full_name=author.display_name or author.name,
                sender_username=author.name,
                message_id=str(message.id),
                chat_id=str(channel.id),
                thread_id=None if thread_id is None else str(thread_id),
                chat_type="private" if guild is None else "group",
                chat_title=getattr(channel, "name", None),
                reply_to=self._reply_block(replied),
                is_explicit_trigger=convert.is_explicit(
                    direct=guild is None,
                    mentioned=mentioned,
                    replied_to_bot=replied_to_bot,
                ),
                is_voice_input=voice,
                skip_mention_check=voice,
                timestamp=message.created_at.isoformat(),
                attachments=attachments or None,
            )
            self.ctx.stats.incr("inbound")
        finally:
            for file in files:
                with contextlib.suppress(OSError):
                    file.unlink()

    async def _replied_message(self, message: discord.Message) -> Any:
        ref = message.reference
        if ref is None:
            return None
        resolved = ref.resolved
        if isinstance(resolved, discord.Message):
            return resolved
        if ref.message_id is None:
            return None
        try:
            return await message.channel.fetch_message(ref.message_id)
        except discord.HTTPException:
            return None

    @staticmethod
    def _reply_block(replied: Any) -> dict[str, Any] | None:
        if replied is None:
            return None
        return convert.reply_block(
            message_id=replied.id,
            text=replied.content,
            author_id=replied.author.id,
            author_name=getattr(replied.author, "display_name", None)
            or getattr(replied.author, "name", None),
        )

    # ------------------------------------------------------------ outbound

    async def _messageable(self, destination: str) -> Any:
        """The channel (or thread) with this id, else the user to message directly."""
        number = int(destination)
        channel = self.client.get_channel(number)
        if channel is None:
            with contextlib.suppress(discord.HTTPException):
                channel = await self.client.fetch_channel(number)
        if channel is not None:
            return channel if hasattr(channel, "send") else None
        user = self.client.get_user(number)
        if user is None:
            with contextlib.suppress(discord.HTTPException):
                user = await self.client.fetch_user(number)
        return user

    async def send(
        self, interface_id: str, payload: dict[str, Any], origin: dict[str, Any]
    ) -> dict[str, Any]:
        destination = convert.destination_of(
            payload.get("interface_path")
        ) or convert.destination_of(origin.get("interface_path"))
        if destination is None:
            return {"ok": False, "error": "no usable discord_bot interface_path"}
        target = await self._messageable(destination)
        if target is None:
            return {"ok": False, "error": f"no Discord channel or user {destination}"}
        reply = convert.usable_reply_id(payload.get("reply_to"), destination)
        text = str(payload.get("text") or "")
        attachments = list(payload.get("attachments") or [])
        if attachments and reply is None:
            # A file answers the message it came with, like the in-tree interface did.
            if convert.destination_of(origin.get("interface_path")) == destination:
                reply = convert.usable_reply_id(origin.get("message_id"), destination)
        chunks = convert.split_text(text)
        if not chunks and not attachments:
            return {"ok": False, "error": "nothing to send"}
        try:
            if attachments:
                for index, item in enumerate(attachments):
                    file = discord.File(
                        str(item["path"]), filename=str(item.get("name") or "") or None
                    )
                    first = chunks[0] if index == 0 and chunks else None
                    await self._post(target, first, file, reply if index == 0 else None)
                rest = chunks[1:]
            else:
                await self._post(target, chunks[0], None, reply)
                rest = chunks[1:]
            for chunk in rest:
                await self._post(target, chunk, None, None)
        except Exception as exc:  # noqa: BLE001 - the Core sees one failed delivery
            self.ctx.stats.incr("errors", kind="outbound")
            self.ctx.log.error(f"send to {destination}: {type(exc).__name__}: {exc}")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        self.ctx.stats.incr("outbound")
        return {"ok": True}

    @staticmethod
    async def _post(
        target: Any, content: str | None, file: discord.File | None, reply: int | None
    ) -> None:
        """Send; quote ``reply`` when it can be found, plain otherwise."""
        if reply is not None and hasattr(target, "fetch_message"):
            try:
                quoted = await target.fetch_message(reply)
            except discord.HTTPException:
                quoted = None
            if quoted is not None:
                await quoted.reply(content, file=file)
                return
        await target.send(content, file=file)

    async def activity(self, interface_id: str, path: str, kind: str) -> None:
        """ "typing..." (Discord has no wording for recording or uploading)."""
        destination = convert.destination_of(path)
        if destination is None or self.client.user is None:
            return
        if convert.is_direct(path):
            user = self.client.get_user(
                int(destination)
            ) or await self.client.fetch_user(int(destination))
            channel_id = (user.dm_channel or await user.create_dm()).id
        else:
            channel_id = int(destination)
        await self.client.http.send_typing(channel_id)

    async def react(
        self, interface_id: str, chat_id: str, message_id: str, emoji: str
    ) -> bool:
        try:
            channel = await self._messageable(chat_id)
            if channel is None or not hasattr(channel, "fetch_message"):
                return False
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(emoji)
        except (discord.HTTPException, ValueError):
            return False
        return True

    async def resolve_names(
        self, interface_id: str, chat_id: str | None, thread_id: str | None
    ) -> dict[str, Any]:
        if not chat_id or not str(chat_id).isdigit():
            return {}
        channel = self.client.get_channel(int(chat_id))
        if channel is None:
            return {"chat_name": str(chat_id)}
        guild = getattr(channel, "guild", None)
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            where = (
                f"{guild.name} · #{parent.name}" if guild and parent else channel.name
            )
            return {"chat_name": where, "message_thread_name": channel.name}
        name = getattr(channel, "name", None)
        if guild is not None and name:
            return {"chat_name": f"{guild.name} · #{name}"}
        recipient = getattr(channel, "recipient", None)
        return {"chat_name": getattr(recipient, "display_name", None) or str(chat_id)}

    async def set_avatar(
        self, interface_id: str, image: bytes | None, version: str | None, force: bool
    ) -> bool:
        if not await self.ctx.config.get("DISCORD_BOT_USE_SYNTH_AVATAR"):
            return False
        user = self.client.user
        if user is None or not self.client.is_ready():
            return False
        try:
            await user.edit(avatar=image)
        except discord.HTTPException as exc:
            self.ctx.log.warning(f"avatar update refused ({exc.status})")
            return False
        return True

    # ------------------------------------------------------------ lifecycle

    async def on_ready(self) -> None:
        if self.registered:
            return  # a reconnect: already known to the Core
        self.registered = True
        user = self.client.user
        await self.ctx.interfaces.register(
            convert.INTERFACE_ID,
            display_name="Discord Bot",
            bot_id=None if user is None else str(user.id),
            bot_username=None if user is None else user.name,
            path_hint=(
                "discord_bot/<guild_id>/<channel_id>[/<thread_id>] or "
                "discord_bot/<user_id> (DM)"
            ),
            description=(
                "Discord servers (guilds) with channels and threads, and direct messages. "
                "To reply where a message came from, reuse its interface_path."
            ),
            resolve_names=True,
            capabilities=["send_file", "send_audio", "activity", "reaction", "avatar"],
        )
        self.ctx.log.info(f"connected to Discord as {user}")


def build_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True  # a privileged intent: the operator enables it
    return discord.Client(intents=intents)


def refusal_reason(problem: BaseException | None) -> str:
    """Why the connection ended, in words for the operator."""
    if isinstance(problem, discord.LoginFailure):
        return "Discord refused the token"
    if isinstance(problem, discord.PrivilegedIntentsRequired):
        return "Discord needs the Message Content intent switched on for this bot"
    return f"could not connect to Discord ({type(problem).__name__})"


async def main(ctx: Context) -> None:
    token = str(await ctx.config.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        ctx.log.warning("DISCORD_BOT_TOKEN is not configured: Discord is idle")
        await ctx.stopping.wait()
        return
    client = build_client()
    bridge = Bridge(ctx, client)
    client.event(bridge.on_message)
    client.event(bridge.on_ready)
    ctx.interfaces.on_send_message(bridge.send)
    ctx.interfaces.on_activity(bridge.activity)
    ctx.interfaces.on_reaction(bridge.react)
    ctx.interfaces.on_resolve_names(bridge.resolve_names)
    ctx.interfaces.on_set_avatar(bridge.set_avatar)
    run = asyncio.ensure_future(client.start(token))
    stopping = asyncio.ensure_future(ctx.stopping.wait())
    try:
        done, _ = await asyncio.wait(
            {run, stopping}, return_when=asyncio.FIRST_COMPLETED
        )
        if run in done:
            reason = refusal_reason(run.exception())
            ctx.log.error(f"{reason}: Discord is idle until it is fixed")
            if bridge.registered:
                await ctx.interfaces.set_state(convert.INTERFACE_ID, False, reason)
            await ctx.stopping.wait()
    finally:
        stopping.cancel()
        with contextlib.suppress(Exception):
            await client.close()
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run
