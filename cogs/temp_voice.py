import logging

import discord
from discord.ext import commands

from i18n import member_language, tr

log = logging.getLogger("rolebot.temp_voice")


class TempVoice(commands.Cog):
    """Join-to-create: joining the hub channel creates a room that is deleted once it is empty."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._creating: set[int] = set()

    def _state(self, guild_id) -> dict:
        state = self.bot.guild_config(guild_id).setdefault("temp_voice", {})
        state.setdefault("hub_channel_id", None)
        state.setdefault("channels", {})
        return state

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Rooms that emptied while the bot was offline get no voice event anymore.
        for guild in self.bot.guilds:
            state = self._state(guild.id)
            for channel_id in list(state["channels"]):
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    state["channels"].pop(channel_id)
                    self.bot.save_config()
                elif not channel.members:
                    await self._delete_room(channel, state)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if before.channel == after.channel:
            return
        state = self._state(member.guild.id)

        # Delete first: someone going from their own empty room to the hub must not be
        # sent back into a room that is about to be removed.
        left = before.channel
        if left is not None and str(left.id) in state["channels"] and not left.members:
            await self._delete_room(left, state)

        joined = after.channel
        if joined is not None and str(joined.id) == str(state.get("hub_channel_id")):
            await self._enter_hub(member, joined, state)

    async def _enter_hub(self, member: discord.Member, hub: discord.VoiceChannel, state: dict) -> None:
        for channel_id, owner_id in state["channels"].items():
            if str(owner_id) == str(member.id):
                existing = member.guild.get_channel(int(channel_id))
                if existing is not None:
                    await self._move(member, existing, state)
                    return

        if member.id in self._creating:
            return
        self._creating.add(member.id)
        try:
            language = member_language(self.bot.guild_config(member.guild.id), member)
            name = tr(language, "temp_voice_name", name=member.display_name)[:100]
            overwrites = dict(hub.overwrites)
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, manage_channels=True
            )
            try:
                room = await member.guild.create_voice_channel(
                    name, category=hub.category, overwrites=overwrites, reason="Join-to-create"
                )
            except discord.HTTPException:
                log.warning("Could not create a room for %s", member, exc_info=True)
                return
            state["channels"][str(room.id)] = str(member.id)
            self.bot.save_config()
            await self._move(member, room, state)
        finally:
            self._creating.discard(member.id)

    async def _move(self, member: discord.Member, room: discord.VoiceChannel, state: dict) -> None:
        try:
            await member.move_to(room, reason="Join-to-create")
        except discord.HTTPException:
            # Usually the member left the hub before the move happened.
            log.info("Could not move %s to %s", member, room)
            if not room.members:
                await self._delete_room(room, state)

    async def _delete_room(self, room: discord.VoiceChannel, state: dict) -> None:
        try:
            await room.delete(reason="Join-to-create room is empty")
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.warning("Could not delete the empty room %s", room, exc_info=True)
            return
        state["channels"].pop(str(room.id), None)
        self.bot.save_config()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVoice(bot))
