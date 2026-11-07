import logging
import re

import discord
from discord.ext import commands

from emoji_utils import is_valid_emoji
from i18n import member_language, tr

log = logging.getLogger("rolebot.reaction_roles")

# Discord allows 5 rows of 5 buttons on one message.
MAX_MENU_ROLES = 25

_INLINE_REF = re.compile(r"(?<![\w<#@/&?])([#@])([A-Za-z0-9_.\-]+)")


def resolve_links(guild: discord.Guild | None, text: str | None) -> str | None:
    if not text or guild is None:
        return text

    def replace(match: re.Match) -> str:
        sigil, name = match.group(1), match.group(2)
        low = name.lower()
        if sigil == "#":
            channel = next(
                (c for c in guild.channels if getattr(c, "name", None) and c.name.lower() == low),
                None,
            )
            found = channel
        else:
            found = next(
                (r for r in guild.roles if not r.is_default() and r.name.lower() == low),
                None,
            ) or next(
                (
                    m
                    for m in guild.members
                    if m.name.lower() == low
                    or m.display_name.lower() == low
                    or (m.nick and m.nick.lower() == low)
                ),
                None,
            )
        return found.mention if found is not None else match.group(0)

    return _INLINE_REF.sub(replace, text)


def build_embed(bot: commands.Bot, menu_spec: dict) -> discord.Embed:
    guild = _menu_guild(bot, menu_spec)
    embed_spec = menu_spec.get("embed") or {}
    embed = discord.Embed(
        title=embed_spec.get("title"),
        description=resolve_links(guild, embed_spec.get("description")),
        color=discord.Color(int(embed_spec.get("color", 0x5865F2))),
    )
    if embed_spec.get("footer"):
        embed.set_footer(text=embed_spec["footer"])
    return embed


def _menu_guild(bot: commands.Bot, menu_spec: dict) -> discord.Guild | None:
    guild_id = menu_spec.get("guild_id")
    if not guild_id:
        return None
    return bot.get_guild(int(guild_id))


def _build_buttons(bot: commands.Bot, menu_spec: dict, guild: discord.Guild | None) -> list["RoleButton"]:
    buttons: list[RoleButton] = []
    style = discord.ButtonStyle.success if menu_spec.get("mode") == "accept" else discord.ButtonStyle.primary
    role_specs = menu_spec.get("roles", [])
    if len(role_specs) > MAX_MENU_ROLES:
        log.warning(
            'Menu "%s" has %d roles, only the first %d fit on one message.',
            menu_spec["id"],
            len(role_specs),
            MAX_MENU_ROLES,
        )
    for role_spec in role_specs[:MAX_MENU_ROLES]:
        role = guild.get_role(int(role_spec["role_id"])) if guild else None
        if role is None or role.is_default() or role.managed:
            continue
        label = (role_spec.get("label") or role.name)[:80]
        emoji = role_spec.get("emoji") if is_valid_emoji(role_spec.get("emoji")) else None
        limit = role_spec.get("limit")
        if limit:
            limit = int(limit)
            holders = len(role.members)
            suffix = f" ({holders}/{limit})"
            label = f"{label[: 80 - len(suffix)]}{suffix}"
        buttons.append(RoleButton(bot, menu_spec["id"], role.id, label, emoji, style))
    return buttons


class RoleButton(discord.ui.Button):
    def __init__(
        self,
        bot: commands.Bot,
        menu_id: str,
        role_id: int,
        label: str,
        emoji: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.primary,
    ) -> None:
        super().__init__(
            label=label or "Role",
            emoji=emoji,
            style=style,
            custom_id=f"role_menu:{menu_id}:{role_id}",
        )
        self.bot = bot
        self.menu_id = menu_id
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(tr("de", "menu_server_only"), ephemeral=True)
            return

        guild_config = self.bot.guild_config(guild.id)
        language = member_language(guild_config, member)

        async def reply(key: str, **values) -> None:
            text = resolve_links(guild, tr(language, key, **values))
            await interaction.response.send_message(text, ephemeral=True)

        role = guild.get_role(self.role_id)
        if role is None:
            await reply("role_gone")
            return
        if role.is_default() or role.managed:
            await reply("role_not_assignable")
            return

        bot_member = guild.me
        if bot_member is None or role >= bot_member.top_role:
            await reply("role_too_high")
            return

        menu = self._menu(guild)
        accept_mode = menu is not None and menu.get("mode") == "accept"
        # The member's cached roles are not updated yet when the reply is sent.
        is_language_role = str(role.id) == str(guild_config.get("international_role_id"))

        try:
            if role in member.roles:
                if accept_mode:
                    await reply("rules_already_accepted")
                    return
                await member.remove_roles(role, reason="Role menu")
                key = "role_removed"
                if is_language_role:
                    language = "de"
            else:
                limit = self._role_limit(guild)
                if limit is not None:
                    holders = [m for m in role.members if isinstance(m, discord.Member)]
                    if len(holders) >= limit:
                        await reply("role_full", role=role.name, limit=limit)
                        return
                await member.add_roles(role, reason="Role menu")
                key = "rules_accepted" if accept_mode else "role_added"
                if is_language_role:
                    language = "en"
        except discord.Forbidden:
            await reply("no_permission")
            return
        except discord.HTTPException:
            await reply("try_again")
            return

        await reply(key, role=role.name)

    def _menu(self, guild: discord.Guild) -> dict | None:
        return next(
            (m for m in self.bot.guild_config(guild.id).get("role_menus", []) if m["id"] == self.menu_id),
            None,
        )

    def _role_limit(self, guild: discord.Guild) -> int | None:
        menu = self._menu(guild)
        if menu is None:
            return None
        spec = next((r for r in menu.get("roles", []) if str(r.get("role_id")) == str(self.role_id)), None)
        if spec is None:
            return None
        limit = spec.get("limit")
        return int(limit) if limit else None


class RoleMenuView(discord.ui.View):
    def __init__(self, bot: commands.Bot, menu_spec: dict) -> None:
        super().__init__(timeout=None)
        self.menu_id = menu_spec["id"]
        self.guild_id = str(menu_spec.get("guild_id") or "")
        for button in _build_buttons(bot, menu_spec, _menu_guild(bot, menu_spec)):
            self.add_item(button)


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.views: dict[tuple[str, str], RoleMenuView] = {}
        self.rebuild_views()

    def _menu_key(self, menu: dict) -> tuple[str, str]:
        return (str(menu.get("guild_id") or ""), menu["id"])

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.roles == after.roles:
            return
        changed = {str(role.id) for role in before.roles} ^ {str(role.id) for role in after.roles}
        await self.refresh_menus_for_roles(after.guild, changed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self.refresh_menus_for_roles(member.guild, {str(role.id) for role in member.roles})

    async def refresh_menus_for_roles(self, guild: discord.Guild, changed_role_ids: set[str]) -> None:
        if not changed_role_ids:
            return
        for menu in self.bot.guild_config(guild.id).get("role_menus", []):
            limited = {str(spec["role_id"]) for spec in menu.get("roles", []) if spec.get("limit")}
            if limited & changed_role_ids:
                await self.refresh_menu(menu)

    def rebuild_views(self) -> None:
        self.views = {}
        for guild_id, config in self.bot.configs.items():
            for menu in config.get("role_menus", []):
                menu.setdefault("guild_id", str(guild_id))
                view = RoleMenuView(self.bot, menu)
                self.views[self._menu_key(menu)] = view
                message_id = menu.get("message_id")
                self.bot.add_view(view, message_id=int(message_id) if message_id else None)

    def _menu_channel(self, menu: dict) -> tuple[discord.TextChannel | None, str]:
        guild = _menu_guild(self.bot, menu)
        if guild is None or not menu.get("channel_id"):
            return None, "no channel is set for this menu"
        channel = guild.get_channel(int(menu["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return None, "the configured channel wasn't found"
        bot_member = guild.me
        perms = channel.permissions_for(bot_member) if bot_member else None
        if perms is None or not (perms.send_messages and perms.embed_links):
            return None, "I can't post embeds in the configured channel"
        return channel, "ok"

    async def refresh_menu(self, menu: dict) -> tuple[bool, str]:
        channel, reason = self._menu_channel(menu)
        if channel is None:
            return False, reason

        embed = build_embed(self.bot, menu)
        view = self.views.get(self._menu_key(menu))
        if view is None:
            view = RoleMenuView(self.bot, menu)
            self.views[self._menu_key(menu)] = view
        else:
            view.clear_items()
            for button in _build_buttons(self.bot, menu, _menu_guild(self.bot, menu)):
                view.add_item(button)
        message_id = menu.get("message_id")
        if message_id:
            try:
                old = await channel.fetch_message(int(message_id))
                await old.edit(embed=embed, view=view)
                return True, "refreshed the existing message"
            except discord.NotFound:
                pass
            except discord.HTTPException:
                return False, "could not edit the existing message"

        try:
            message = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            return False, "I couldn't post in the channel"
        menu["message_id"] = str(message.id)
        self.bot.save_config()
        return True, "posted"

    async def move_menu(self, menu: dict, channel_id: int) -> tuple[bool, str]:
        guild = _menu_guild(self.bot, menu)
        old_channel = None
        if guild is not None and menu.get("channel_id"):
            old_channel = guild.get_channel(int(menu["channel_id"]))
        old_message_id = menu.get("message_id")

        menu["channel_id"] = str(channel_id)
        menu["message_id"] = None
        if old_channel is not None and old_message_id:
            try:
                old = await old_channel.fetch_message(int(old_message_id))
                await old.delete()
            except discord.HTTPException:
                pass
        self.bot.save_config()
        return await self.refresh_menu(menu)

    async def refresh_all(self, guild: discord.Guild | None = None) -> tuple[int, int]:
        if guild is not None:
            menus = self.bot.guild_config(guild.id).get("role_menus", [])
        else:
            menus = [menu for config in self.bot.configs.values() for menu in config.get("role_menus", [])]
        posted = failed = 0
        for menu in menus:
            ok, _ = await self.refresh_menu(menu)
            if ok:
                posted += 1
            else:
                failed += 1
        return posted, failed

    async def unpost_menu(self, menu: dict) -> None:
        channel = self._menu_channel(menu)[0]
        message_id = menu.get("message_id")
        if channel is not None and message_id:
            try:
                old = await channel.fetch_message(int(message_id))
                await old.delete()
            except discord.HTTPException:
                pass
        menu["message_id"] = None
        self.bot.save_config()

    async def delete_menu(self, menu: dict) -> None:
        channel = self._menu_channel(menu)[0]
        message_id = menu.get("message_id")
        if channel is not None and message_id:
            try:
                old = await channel.fetch_message(int(message_id))
                await old.delete()
            except discord.HTTPException:
                pass
        config = self.bot.guild_config(menu.get("guild_id"))
        config["role_menus"].remove(menu)
        self.bot.save_config()
        self.rebuild_views()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))