import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("rolebot.grant")


def grant_config(bot: commands.Bot, guild_id) -> dict:
    grant = bot.guild_config(guild_id).setdefault("grant_permissions", {})
    grant.setdefault("users", {})
    grant.setdefault("roles", {})
    return grant


def role_grant_problem(role: discord.Role, top_role: discord.Role) -> str | None:
    """Reason a role can't be handed out by someone whose highest role is top_role."""
    if role.is_default():
        return "the @everyone role can't be granted"
    if role.managed:
        return "integration-managed roles can't be granted"
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


def can_grant(bot: commands.Bot, member: discord.Member, role: discord.Role) -> tuple[bool, str]:
    if not has_grant_permission(bot, member, role):
        return False, f"You don't have permission to grant {role.mention}."
    problem = role_grant_problem(role, member.top_role)
    if problem is not None:
        return False, f"You can't grant {role.mention}: {problem}."
    if not role.is_assignable():
        return False, f"I can't assign {role.mention} — it's above my highest role."
    return True, ""


class Grant(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="grant", description="Give one or more members a role")
    @app_commands.guild_only()
    @app_commands.describe(
        user1="The member to give the role to",
        role="The role to give them",
        user2="Another member to give the role to (optional)",
        user3="Another member to give the role to (optional)",
    )
    async def grant_cmd(
        self,
        interaction: discord.Interaction,
        user1: discord.Member,
        role: discord.Role,
        user2: discord.Member | None = None,
        user3: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works inside a server.", ephemeral=True)
            return
        invoker = interaction.user
        if not isinstance(invoker, discord.Member):
            await interaction.response.send_message(
                "This command only works inside a server.", ephemeral=True
            )
            return

        allowed, reason = can_grant(self.bot, invoker, role)
        if not allowed:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        targets = list({member.id: member for member in (user1, user2, user3) if member is not None}.values())
        granted: list[discord.Member] = []
        already: list[discord.Member] = []
        failed: list[tuple[discord.Member, str]] = []
        for target in targets:
            if role in target.roles:
                already.append(target)
                continue
            try:
                await target.add_roles(role, reason=f"Granted by {invoker} via /grant", atomic=True)
                granted.append(target)
            except discord.Forbidden:
                log.warning("Cannot grant %s to %s in %s (role hierarchy)", role, target, interaction.guild)
                failed.append((target, "missing permission or role hierarchy"))
            except discord.HTTPException:
                log.warning("Failed to grant %s to %s", role, target, exc_info=True)
                failed.append((target, "Discord API error"))

        embed = build_grant_result_embed(invoker, role, granted, already, failed)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log(interaction.guild, invoker, role, granted, failed)

    async def _log(
        self,
        guild: discord.Guild,
        invoker: discord.Member,
        role: discord.Role,
        granted: list[discord.Member],
        failed: list[tuple[discord.Member, str]],
    ) -> None:
        log_channel_id = self.bot.guild_config(guild.id).get("log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if channel is None:
            return

        embed = discord.Embed(title="Role granted", color=0x5865F2, timestamp=discord.utils.utcnow())
        embed.add_field(name="By", value=f"{invoker.mention} (`{invoker.id}`)", inline=False)
        embed.add_field(name="Role", value=role.mention, inline=False)
        embed.add_field(
            name="Members",
            value="\n".join(member.mention for member in granted) or "none",
            inline=False,
        )
        if failed:
            embed.add_field(
                name="Failed",
                value="\n".join(f"{member.mention} — {reason}" for member, reason in failed),
                inline=False,
            )
        embed.set_footer(text=f"{invoker} • {invoker.id}")

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not send grant log to %s", channel, exc_info=True)


def build_grant_result_embed(
    invoker: discord.Member,
    role: discord.Role,
    granted: list[discord.Member],
    already: list[discord.Member],
    failed: list[tuple[discord.Member, str]],
) -> discord.Embed:
    embed = discord.Embed(
        title="Role grant",
        description=f"Granting {role.mention} as {invoker.mention}",
        color=0x5865F2,
    )
    if granted:
        embed.add_field(
            name=f"Granted ({len(granted)})",
            value="\n".join(member.mention for member in granted)[:1024],
            inline=False,
        )
    if already:
        embed.add_field(
            name=f"Already had it ({len(already)})",
            value="\n".join(member.mention for member in already)[:1024],
            inline=False,
        )
    if failed:
        embed.add_field(
            name=f"Failed ({len(failed)})",
            value="\n".join(f"{member.mention} — {reason}" for member, reason in failed)[:1024],
            inline=False,
        )
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Grant(bot))
