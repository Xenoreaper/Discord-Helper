import logging

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.join")


class JoinRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.guild_id = int(bot.config["guild_id"])
        self._invite_uses: dict[int, dict[str, int]] = {}

    def _relevant(self, guild: discord.Guild) -> bool:
        return guild.id == self.guild_id

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

    async def _detect_invite(self, guild: discord.Guild) -> str | None:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        old = self._invite_uses.get(guild.id, {})
        best_code: str | None = None
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
                best_code, best_delta = inv.code, delta

        self._invite_uses[guild.id] = {inv.code: (inv.uses or 0) for inv in invites}
        return best_code if best_delta > 0 else None

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
            if self._relevant(guild):
                await self._cache_invites(guild)
        if self.bot.get_guild(self.guild_id) is None:
            log.warning("The bot is not in the guild configured as guild_id (%s)", self.guild_id)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self._relevant(guild):
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not self._relevant(member.guild):
            return

        join_config = self.bot.config.get("join", {})
        roles: list[discord.Role] = []

        base_role_id = join_config.get("base_role_id")
        if base_role_id:
            base_role = member.guild.get_role(int(base_role_id))
            if base_role is not None:
                roles.append(base_role)

        invite_roles = join_config.get("invites", {})
        used_code = await self._detect_invite(member.guild)
        if used_code is not None and used_code in invite_roles:
            invite_role = member.guild.get_role(int(invite_roles[used_code]))
            if invite_role is not None:
                roles.append(invite_role)
                log.info("%s joined via invite %s, granting %s", member, used_code, invite_role.name)

        if roles:
            await self._grant_roles(member, roles)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinRoles(bot))
