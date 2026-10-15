import logging

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.guard")


class MessageGuard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        guild_config = self.bot.guild_config(message.guild.id)
        kick_channel_id = guild_config.get("kick_channel_id")
        if not kick_channel_id:
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

        exempt_roles = guild_config.get("kick_exempt_roles", []) + guild_config.get("admin_roles", [])
        if any(author.get_role(int(role_id)) is not None for role_id in exempt_roles or []):
            return

        try:
            await message.delete()
        except discord.HTTPException:
            log.warning("Could not delete a message in %s", message.channel, exc_info=True)

        action = guild_config.get("kick_action") or "kick"
        reason = f"Posted in {message.channel.mention} (no-chat channel)"
        await self._punish(message, author, action, reason)

    async def _punish(
        self, message: discord.Message, author: discord.Member, action: str, reason: str
    ) -> None:
        if action == "ban":
            verb, past, permission = "ban", "banned", "Ban Members"
            call = author.ban(reason=reason)
        else:
            verb, past, permission = "kick", "kicked", "Kick Members"
            call = author.kick(reason=reason)

        try:
            await call
        except discord.Forbidden:
            log.warning(
                "Cannot %s %s in %s — check my %s permission and role hierarchy.",
                verb,
                author,
                message.guild,
                permission,
            )
            await self._log_action(message, author, action, reason, success=False, error="Missing permission or role hierarchy")
            return
        except discord.HTTPException:
            log.warning("Failed to %s %s", verb, author, exc_info=True)
            await self._log_action(message, author, action, reason, success=False, error="Discord API error")
            return

        log.info("%s %s for posting in %s", past.capitalize(), author, message.channel)
        await self._log_action(message, author, action, reason, success=True)

    async def _log_action(
        self,
        message: discord.Message,
        author: discord.Member,
        action: str,
        reason: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        log_channel_id = self.bot.guild_config(message.guild.id).get("log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if channel is None:
            return

        verb = "banned" if action == "ban" else "kicked"
        embed = discord.Embed(
            title=f"Member {verb}" if success else f"{'Ban' if action == 'ban' else 'Kick'} failed",
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
            log.warning("Could not send moderation log to %s", channel, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageGuard(bot))
