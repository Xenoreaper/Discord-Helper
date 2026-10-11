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
        self,
        member: discord.Member,
        invite: discord.Invite | None,
        invite_role: discord.Role | None,
        skip_reason: str | None = None,
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
        if skip_reason:
            embed.add_field(name="Note", value=f"Invite role skipped — {skip_reason}", inline=False)
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
        skip_reason: str | None = None
        if used_code in invite_roles:
            spec = _invite_spec(invite_roles[used_code])
            if spec is None:
                log.warning("Invite %s has an invalid role mapping in the config", used_code)
            else:
                candidate = member.guild.get_role(int(spec["role_id"]))
                if candidate is None:
                    log.warning("Invite %s points to a role that no longer exists", used_code)
                else:
                    allowed, reason = _invite_allows(spec, invite)
                    if allowed:
                        roles.append(candidate)
                        invite_role = candidate
                        if _consume_used(spec):
                            self.bot.save_config()
                        log.info("%s joined via invite %s, granting %s", member, used_code, candidate.name)
                    else:
                        skip_reason = reason

        if roles:
            await self._grant_roles(member, roles)

        await self._log_join(member, invite, invite_role, skip_reason)


def _invite_spec(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if _is_role_id(raw):
        return {"role_id": str(raw)}
    return None


def _is_role_id(value) -> bool:
    return value not in (None, "") and str(value).isdigit()


def _invite_allows(spec: dict, invite: discord.Invite | None) -> tuple[bool, str | None]:
    window_days = spec.get("within_days")
    if window_days:
        created = invite.created_at if invite is not None else None
        if created is not None:
            age = (discord.utils.utcnow() - created).total_seconds()
            if age > window_days * 86400:
                return False, f"over the {window_days}d window for this invite"
    max_uses = spec.get("max_uses")
    if max_uses and (spec.get("used") or 0) >= max_uses:
        return False, f"this invite has already granted the role {max_uses} times"
    return True, None


def _consume_used(spec: dict) -> bool:
    if not spec.get("max_uses"):
        return False
    spec["used"] = (spec.get("used") or 0) + 1
    return True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinRoles(bot))
