import logging

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.guard")


class MessageGuard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        kick_channel_id = self.bot.config.get("kick_channel_id")
        if message.guild is None or not kick_channel_id:
            return
        if message.channel.id != int(kick_channel_id):
            return

        if getattr(message, "is_system", lambda: False)():
            return

        author = message.author
        if not isinstance(author, discord.Member) or author.bot:
            return

        perms = author.guild_permissions
        if perms.administrator or perms.manage_messages or perms.kick_members:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("Could not delete a message in %s", message.channel, exc_info=True)

        reason = f"Posted in {message.channel.mention} (no-chat channel)"
        try:
            await author.kick(reason=reason)
            log.info("Kicked %s for posting in %s", author, message.channel)
        except discord.Forbidden:
            log.warning(
                "Cannot kick %s in %s — check my Kick Members permission and role hierarchy.",
                author,
                message.guild,
            )
            await self._log_kick(message, author, reason, success=False, error="Missing permission or role hierarchy")
            return
        except discord.HTTPException:
            log.warning("Failed to kick %s", author, exc_info=True)
            await self._log_kick(message, author, reason, success=False, error="Discord API error")
            return

        await self._log_kick(message, author, reason, success=True)

    async def _log_kick(
        self, message: discord.Message, author: discord.Member, reason: str, success: bool, error: str | None = None
    ) -> None:
        log_channel_id = self.bot.config.get("log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if channel is None:
            return

        embed = discord.Embed(
            title="Member kicked" if success else "Kick failed",
            color=discord.Color.red() if success else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"{author.mention} (`{author.id}`)", inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        if error:
            embed.add_field(name="Error", value=error, inline=False)
        embed.set_footer(text=f"{author} • {author.id}")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not send kick log to %s", channel, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageGuard(bot))
