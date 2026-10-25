import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("rolebot.grant")

# action -> (past tense for a successful log, present tense for a failed log)
_ACTIONS = {
    "grant": ("granted", "grant"),
    "revoke": ("revoked", "revoke"),
}


def grant_config(bot: commands.Bot, guild_id) -> dict:
    grant = bot.guild_config(guild_id).setdefault("grant_permissions", {})
    grant.setdefault("users", {})
    grant.setdefault("roles", {})
    return grant


def role_grant_problem(role: discord.Role, top_role: discord.Role, action: str = "grant") -> str | None:
    """Reason a role can't be handed out (or taken away) by someone whose highest role is top_role."""
    past = _ACTIONS.get(action, _ACTIONS["grant"])[0]
    if role.is_default():
        return f"the @everyone role can't be {past}"
    if role.managed:
        return f"integration-managed roles can't be {past}"
    if role.position >= top_role.position:
        return f"{role.mention} is at or above the highest role"
    return None


def has_grant_permission(bot: commands.Bot, member: discord.Member, role: discord.Role) -> bool:
    if bot.is_bot_admin(member):
        return True
    grant = grant_config(bot, member.guild.id)
    if str(role.id) in grant["users"].get(str(member.id), []):
        return True
    return any(
        str(role.id) in grant["roles"].get(str(held.id), [])
        for held in member.roles
    )


def can_manage(
    bot: commands.Bot, member: discord.Member, role: discord.Role, action: str = "grant"
) -> tuple[bool, str]:
    """Whether `member` may grant/revoke `role`. Granting and revoking share permissions."""
    if not has_grant_permission(bot, member, role):
        return False, f"You don't have permission to {action} {role.mention}."
    problem = role_grant_problem(role, member.top_role, action)
    if problem is not None:
        return False, f"You can't {action} {role.mention}: {problem}."
    if not role.is_assignable():
        return False, f"I can't {action} {role.mention} — it's above my highest role."
    return True, ""


def can_grant(bot: commands.Bot, member: discord.Member, role: discord.Role) -> tuple[bool, str]:
    return can_manage(bot, member, role, "grant")


class Grant(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _invoker(self, interaction: discord.Interaction) -> discord.Member | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return None
        return interaction.user

    @app_commands.command(name="grant", description="Give a member a role")
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member to give the role to",
        role="The role to give them",
    )
    async def grant_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: discord.Role,
    ) -> None:
        invoker = await self._invoker(interaction)
        if invoker is None:
            return

        allowed, reason = can_manage(self.bot, invoker, role, "grant")
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        if role in user.roles:
            await interaction.response.send_message(
                f"{user.mention} already has {role.mention}.", ephemeral=True
            )
            return

        try:
            await user.add_roles(role, reason=f"Granted by {invoker} via /grant", atomic=True)
        except discord.Forbidden:
            log.warning("Cannot grant %s to %s in %s (role hierarchy)", role, user, interaction.guild)
            await interaction.response.send_message(
                f"I couldn't give {role.mention} to {user.mention} — "
                "check my Manage Roles permission and that my role is above it.",
                ephemeral=True,
            )
            await self._log(interaction.guild, invoker, user, role, success=False, action="grant")
            return
        except discord.HTTPException:
            log.warning("Failed to grant %s to %s", role, user, exc_info=True)
            await interaction.response.send_message(
                "Discord rejected that — please try again.", ephemeral=True
            )
            await self._log(interaction.guild, invoker, user, role, success=False, action="grant")
            return

        await interaction.response.send_message(
            f"Gave {role.mention} to {user.mention}.", ephemeral=True
        )
        await self._log(interaction.guild, invoker, user, role, success=True, action="grant")

    @app_commands.command(name="revoke", description="Take a role away from a member")
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member to take the role from",
        role="The role to take away",
    )
    async def revoke_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: discord.Role,
    ) -> None:
        invoker = await self._invoker(interaction)
        if invoker is None:
            return

        allowed, reason = can_manage(self.bot, invoker, role, "revoke")
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        if role not in user.roles:
            await interaction.response.send_message(
                f"{user.mention} doesn't have {role.mention}.", ephemeral=True
            )
            return

        try:
            await user.remove_roles(role, reason=f"Revoked by {invoker} via /revoke", atomic=True)
        except discord.Forbidden:
            log.warning("Cannot revoke %s from %s in %s (role hierarchy)", role, user, interaction.guild)
            await interaction.response.send_message(
                f"I couldn't remove {role.mention} from {user.mention} — "
                "check my Manage Roles permission and that my role is above it.",
                ephemeral=True,
            )
            await self._log(interaction.guild, invoker, user, role, success=False, action="revoke")
            return
        except discord.HTTPException:
            log.warning("Failed to revoke %s from %s", role, user, exc_info=True)
            await interaction.response.send_message(
                "Discord rejected that — please try again.", ephemeral=True
            )
            await self._log(interaction.guild, invoker, user, role, success=False, action="revoke")
            return

        await interaction.response.send_message(
            f"Removed {role.mention} from {user.mention}.", ephemeral=True
        )
        await self._log(interaction.guild, invoker, user, role, success=True, action="revoke")

    async def _log(
        self,
        guild: discord.Guild,
        invoker: discord.Member,
        user: discord.Member,
        role: discord.Role,
        success: bool,
        action: str = "grant",
    ) -> None:
        log_channel_id = self.bot.guild_config(guild.id).get("log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if channel is None:
            return

        past, noun = _ACTIONS.get(action, _ACTIONS["grant"])
        embed = discord.Embed(
            title=(f"Role {past}" if success else f"Role {noun} failed"),
            color=0x5865F2 if success else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="By", value=f"{invoker.mention} (`{invoker.id}`)", inline=False)
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="Role", value=role.mention, inline=False)
        embed.set_footer(text=f"{invoker} • {invoker.id}")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not send %s log to %s", noun, channel, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Grant(bot))
