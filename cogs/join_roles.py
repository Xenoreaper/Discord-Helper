import logging

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.join")


class JoinRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._invite_uses: dict[int, dict[str, int]] = {}

    async def _cache_invites(self, guild: discord.Guild) -> None:
        try:
            invites = await guild.invites()
        except discord.Forbidden:
            log.warning(
                "Cannot fetch invites for %s (missing Manage Guild). "
                "Invite-based join roles will not work until the cache warms up.",
                guild.name,
            )
            return
        except discord.HTTPException:
            log.warning("Failed to fetch invites for %s", guild.name, exc_info=True)
            return
        self._invite_uses[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}

    async def _detect_invite(self, guild: discord.Guild) -> discord.Invite | None:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        old = self._invite_uses.get(guild.id, {})
        best_invite: discord.Invite | None = None
        best_delta = 0
        for inv in invites:
            uses = inv.uses or 0
            previous = old.get(inv.code)
            if previous is None:
                if not old:
                    continue
                delta = uses
            else:
                delta = uses - previous
            if delta > best_delta:
                best_invite, best_delta = inv, delta

        self._invite_uses[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}
        return best_invite if best_delta > 0 else None

    async def _log_join(
        self, member: discord.Member, invite: discord.Invite | None, invite_role: discord.Role | None
    ) -> None:
        log_channel_id = self.bot.guild_config(member.guild.id).get("log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if channel is None:
            return

        embed = discord.Embed(
            title="Member joined",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=False)
        if invite is not None:
            embed.add_field(name="Invite", value=f"`{invite.code}`", inline=False)
            if invite.channel is not None:
                embed.add_field(name="Channel", value=invite.channel.mention, inline=False)
            if invite.inviter is not None:
                embed.add_field(
                    name="Inviter",
                    value=f"{invite.inviter.mention} (`{invite.inviter.id}`)",
                    inline=False,
                )
        else:
            embed.add_field(name="Invite", value="Unknown (could not detect)", inline=False)
        embed.add_field(
            name="Invite role",
            value=invite_role.mention if invite_role is not None else "None",
            inline=False,
        )
        embed.set_footer(text=f"{member} • {member.id}")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not send join log to %s", channel, exc_info=True)

    async def _grant_roles(self, member: discord.Member, roles: list[discord.Role]) -> None:
        try:
            await member.add_roles(*roles, reason="Joined the server", atomic=True)
        except discord.Forbidden:
            log.warning(
                "Missing permissions to assign %s to %s in %s",
                ", ".join(role.name for role in roles),
                member,
                member.guild.name,
            )
        except discord.HTTPException:
            log.warning("Failed to assign join roles to %s", member, exc_info=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            self.bot.guild_config(guild.id)
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        join_config = self.bot.guild_config(member.guild.id).get("join", {})
        roles: list[discord.Role] = []

        base_role_id = join_config.get("base_role_id")
        if base_role_id:
            base_role = member.guild.get_role(int(base_role_id))
            if base_role is not None:
                roles.append(base_role)

        invite_roles = join_config.get("invites", {})
        invite = await self._detect_invite(member.guild)
        used_code = invite.code if invite is not None else None
        invite_role: discord.Role | None = None
        if used_code is not None and used_code in invite_roles:
            invite_role = member.guild.get_role(int(invite_roles[used_code]))
            if invite_role is not None:
                roles.append(invite_role)
                log.info("%s joined via invite %s, granting %s", member, used_code, invite_role.name)

        if roles:
            await self._grant_roles(member, roles)

        await self._log_join(member, invite, invite_role)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinRoles(bot))
