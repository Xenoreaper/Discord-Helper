import logging

import discord
from discord import app_commands
from discord.ext import commands

from emoji_utils import MAX_EMOJI_LEN, is_valid_emoji

log = logging.getLogger("rolebot.settings")

_SAVE_FAILED = "Could not save config.json — the change is in memory only and will be lost on restart."


def _channel_mention(bot: commands.Bot, channel_id) -> str:
    if not channel_id:
        return "not set"
    channel = bot.get_channel(int(channel_id))
    return channel.mention if channel else f"`{channel_id}` (not found)"


def _role_mention(guild: discord.Guild, role_id) -> str:
    if not role_id:
        return "not set"
    role = guild.get_role(int(role_id))
    return role.mention if role else f"`{role_id}` (not found)"


def _invite_text(bot: commands.Bot, guild: discord.Guild) -> str:
    mappings = bot.guild_config(guild.id).get("join", {}).get("invites", {})
    if not mappings:
        return "none"
    lines = []
    for code, spec in mappings.items():
        role_id = spec if isinstance(spec, str) else spec.get("role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        label = role.mention if role else f"`{role_id}`"
        if isinstance(spec, dict) and (spec.get("max_uses") or spec.get("within_days")):
            bits = []
            if spec.get("max_uses"):
                bits.append(f"used {spec.get('used') or 0}/{spec['max_uses']}")
            if spec.get("within_days"):
                bits.append(f"{spec['within_days']}d window")
            label += f" ({', '.join(bits)})"
        lines.append(f"`{code}` -> {label}")
    return "\n".join(lines)[:1024] or "none"


def _role_list_text(bot: commands.Bot, guild: discord.Guild, key: str) -> str:
    role_ids = bot.guild_config(guild.id).get(key, [])
    if not role_ids:
        return "none"
    lines = []
    for role_id in role_ids:
        role = guild.get_role(int(role_id))
        lines.append(role.mention if role else f"`{role_id}` (not found)")
    return "\n".join(lines)[:1024]


def _exempt_roles_text(bot: commands.Bot, guild: discord.Guild) -> str:
    return _role_list_text(bot, guild, "kick_exempt_roles")


def _get_menu(bot: commands.Bot, guild: discord.Guild | None, menu_id: str) -> dict | None:
    if guild is None:
        return None
    return next(
        (menu for menu in bot.guild_config(guild.id).get("role_menus", []) if menu["id"] == menu_id),
        None,
    )


def _menu_post_link(bot: commands.Bot, menu: dict) -> str | None:
    guild_id = menu.get("guild_id")
    channel = menu.get("channel_id")
    message = menu.get("message_id")
    if not (guild_id and channel and message):
        return None
    return f"https://discord.com/channels/{guild_id}/{channel}/{message}"


def _parse_color(value: str) -> int | None:
    value = value.strip().lstrip("#")
    if not value:
        return None
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return int(value, 16)
    if value.isdigit() and int(value) <= 0xFFFFFF:
        return int(value)
    raise ValueError(value)


def _parse_optional_int(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(value)
    amount = int(text)
    return amount if amount >= 1 else None


def build_settings_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    config = bot.guild_config(guild.id)
    join = config.get("join", {})
    embed = discord.Embed(title="Server Settings", color=0x5865F2)
    embed.add_field(name="Kick channel", value=_channel_mention(bot, config.get("kick_channel_id")), inline=False)
    embed.add_field(name="Admin log channel", value=_channel_mention(bot, config.get("log_channel_id")), inline=False)
    embed.add_field(name="Bot admin roles", value=_role_list_text(bot, guild, "admin_roles"), inline=False)
    embed.add_field(name="Kick-exempt roles", value=_exempt_roles_text(bot, guild), inline=False)
    embed.add_field(name="Default role (on join)", value=_role_mention(guild, join.get("base_role_id")), inline=False)
    embed.add_field(name="Invite roles", value=_invite_text(bot, guild), inline=False)
    menus = config.get("role_menus", [])
    if menus:
        lines = []
        for menu in menus:
            channel = guild.get_channel(int(menu["channel_id"])) if menu.get("channel_id") else None
            name = menu.get("name") or menu["id"]
            posted = "posted" if menu.get("message_id") else "not posted"
            lines.append(f"{name} — {channel.mention if channel else 'no channel'} ({len(menu.get('roles', []))} roles, {posted})")
        value = "\n".join(lines)
    else:
        value = "none — create one in the Role menus panel"
    embed.add_field(name="Role menus", value=value, inline=False)
    easter_eggs = config.get("easter_eggs", {})
    easter_enabled = easter_eggs.get("enabled", True)
    embed.add_field(name="Easter eggs", value="on" if easter_enabled else "off", inline=False)
    embed.set_footer(text="Manage role menus in the dedicated panel. Dismiss this message when done.")
    return embed


def build_menus_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    menus = bot.guild_config(guild.id).get("role_menus", [])
    embed = discord.Embed(
        title="Role menus",
        description="Menus are only posted here — nothing gets posted by accident. "
        "Pick one below to manage it, or repost/refresh all of them.",
        color=0x5865F2,
    )
    if not menus:
        embed.add_field(name="No menus yet", value="Press **New menu** to create your first one.", inline=False)
        return embed
    for menu in menus:
        channel = guild.get_channel(int(menu["channel_id"])) if menu.get("channel_id") else None
        name = menu.get("name") or menu["id"]
        role_count = len(menu.get("roles", []))
        value = f"{channel.mention if channel else 'no channel set'} · {role_count} role{'s' if role_count != 1 else ''}"
        link = _menu_post_link(bot, menu)
        value += f"\n**Posted** — [see message]({link})" if link else "\nNot posted yet"
        embed.add_field(name=name, value=value, inline=False)
    return embed


def build_menu_editor_embed(bot: commands.Bot, menu: dict) -> discord.Embed:
    guild = bot.get_guild(int(menu["guild_id"])) if menu.get("guild_id") else None
    channel = guild.get_channel(int(menu["channel_id"])) if (guild and menu.get("channel_id")) else None
    embed_spec = menu.get("embed") or {}
    embed = discord.Embed(title=menu.get("name") or menu["id"], color=0x5865F2)
    embed.add_field(name="Channel", value=channel.mention if channel else "not set", inline=False)
    link = _menu_post_link(bot, menu)
    embed.add_field(name="Status", value=f"[Posted message]({link})" if link else "Not posted", inline=False)
    lines = []
    if guild:
        for role_spec in menu.get("roles", []):
            role = guild.get_role(int(role_spec["role_id"]))
            label = role_spec.get("label") or (role.name if role else role_spec["role_id"])
            emoji = role_spec.get("emoji") if is_valid_emoji(role_spec.get("emoji")) else None
            limit = role_spec.get("limit")
            suffix = f" · max {limit}" if limit else ""
            line = f"{emoji} **{label}**{suffix}" if emoji else f"• **{label}**{suffix}"
            lines.append(line)
    embed.add_field(name="Roles", value="\n".join(lines) or "none", inline=False)
    appearance = embed_spec.get("title") or "*no title*"
    if embed_spec.get("description"):
        appearance += f"\n{embed_spec['description'][:200]}"
    if embed_spec.get("footer"):
        appearance += f"\nFooter: {embed_spec['footer'][:100]}"
    embed.add_field(name="Appearance", value=appearance, inline=False)
    embed.set_footer(text=f"id: {menu['id']} · changes refresh the posted message automatically")
    return embed


def build_invites_embed(bot: commands.Bot, guild: discord.Guild, pending: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="Invite roles",
        description="Map an invite link to a role. Members who join with that invite get the role.",
        color=0x5865F2,
    )
    embed.add_field(name="Configured", value=_invite_text(bot, guild), inline=False)
    if pending:
        embed.add_field(name="Pending", value=f"Now pick the role for invite `{pending}`.", inline=False)
    return embed


def build_clear_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    embed = build_settings_embed(bot, guild)
    embed.title = "Clear a setting"
    embed.description = "Pick which setting to reset to 'none'."
    return embed


def build_roles_panel_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    embed = build_settings_embed(bot, guild)
    embed.title = "Member & admin roles"
    embed.description = (
        "Select roles to add them — current roles are pre-selected, so deselect to remove "
        "specific ones (submit empty to remove all). Admin roles can open /settings and bypass "
        "the channel kick automatically; kick-exempt roles only bypass the kick."
    )
    return embed


async def _fetch_invites(guild: discord.Guild) -> list[discord.Invite]:
    try:
        return await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.warning("Could not fetch invites for %s", guild.name, exc_info=True)
        return []


class _InviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, invites: list[discord.Invite]) -> None:
        options = []
        for invite in invites[:25]:
            inviter = invite.inviter
            label = f"{inviter} • {invite.uses} uses • {invite.code}" if inviter else f"{invite.uses} uses • {invite.code}"
            options.append(discord.SelectOption(label=label[:100], value=invite.code))
        super().__init__(placeholder="Select an invite to map to a role", options=options)
        self.bot = bot

async def callback(self, interaction: discord.Interaction) -> None:
        code = self.values[0]
        invites = await _fetch_invites(interaction.guild)
        await interaction.response.edit_message(
            embed=build_invites_embed(self.bot, interaction.guild),
            view=InviteRolesView(self.bot, invites, interaction.guild, pending_invite=code),
        )


class _RoleForInviteSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, code: str, guild: discord.Guild) -> None:
        super().__init__(placeholder=f"Pick the role for invite {code}", min_values=1, max_values=1)
        self.bot = bot
        self.code = code
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        self.bot.guild_config(self.guild.id).setdefault("join", {}).setdefault("invites", {})[
            self.code
        ] = str(role.id)
        saved = self.bot.save_config()
        invites = await _fetch_invites(interaction.guild)
        await interaction.response.edit_message(
            embed=build_invites_embed(self.bot, interaction.guild),
            view=InviteRolesView(self.bot, invites, guild=self.guild),
        )
        if not saved:
            await interaction.followup.send(_SAVE_FAILED, ephemeral=True)


class _RemoveInviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        options = []
        for code, spec in bot.guild_config(guild.id).get("join", {}).get("invites", {}).items():
            role_id = spec if isinstance(spec, str) else spec.get("role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            label = f"{code} -> {role.name if role else role_id}"
            options.append(discord.SelectOption(label=label[:100], value=code))
        super().__init__(placeholder="Remove an invite mapping", options=options[:25])
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        code = self.values[0]
        self.bot.guild_config(self.guild.id).get("join", {}).get("invites", {}).pop(code, None)
        saved = self.bot.save_config()
        invites = await _fetch_invites(interaction.guild)
        await interaction.response.edit_message(
            embed=build_invites_embed(self.bot, interaction.guild),
            view=InviteRolesView(self.bot, invites, guild=self.guild),
        )
        if not saved:
            await interaction.followup.send(_SAVE_FAILED, ephemeral=True)


class InviteLimitModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, code: str) -> None:
        super().__init__(title=f"Limits for invite {code}"[:45])
        self.bot = bot
        self.guild = guild
        self.code = code
        spec = bot.guild_config(guild.id).get("join", {}).get("invites", {}).get(code)
        if not isinstance(spec, dict):
            spec = {}
        self.max_input = discord.ui.TextInput(
            label="Max uses (0 = unlimited)",
            required=False,
            max_length=9,
            default=str(spec["max_uses"]) if spec.get("max_uses") else None,
        )
        self.window_input = discord.ui.TextInput(
            label="Grant role within days (0 = always)",
            required=False,
            max_length=9,
            default=str(spec["within_days"]) if spec.get("within_days") else None,
        )
        self.add_item(self.max_input)
        self.add_item(self.window_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            max_uses = _parse_optional_int(self.max_input.value)
            window_days = _parse_optional_int(self.window_input.value)
        except ValueError:
            await interaction.response.send_message("Limits must be whole numbers (days/uses).", ephemeral=True)
            return

        invites = self.bot.guild_config(self.guild.id).setdefault("join", {}).setdefault("invites", {})
        raw = invites.get(self.code)
        spec = dict(raw) if isinstance(raw, dict) else {"role_id": raw}
        for field, value in (("max_uses", max_uses), ("within_days", window_days)):
            if value is None:
                spec.pop(field, None)
            else:
                spec[field] = value
        invites[self.code] = spec

        saved = self.bot.save_config()
        invites_list = await _fetch_invites(interaction.guild)
        await interaction.response.edit_message(
            embed=build_invites_embed(self.bot, interaction.guild),
            view=InviteRolesView(self.bot, invites_list, self.guild),
        )
        if not saved:
            await interaction.followup.send(_SAVE_FAILED, ephemeral=True)


class _LimitInviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        options = []
        for code, spec in bot.guild_config(guild.id).get("join", {}).get("invites", {}).items():
            role_id = spec if isinstance(spec, str) else spec.get("role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            label = f"{code} -> {role.name if role else role_id}"
            options.append(discord.SelectOption(label=label[:100], value=code))
        super().__init__(placeholder="Limit an invite's role", options=options[:25])
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(InviteLimitModal(self.bot, self.guild, self.values[0]))


class _BackToSettingsButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=build_settings_embed(self.bot, interaction.guild),
            view=SettingsPanelView(self.bot, interaction.guild),
        )


class InviteRolesView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        invites: list[discord.Invite],
        guild: discord.Guild,
        pending_invite: str | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        if pending_invite is not None:
            self.add_item(_RoleForInviteSelect(bot, pending_invite, guild))
        else:
            if invites:
                self.add_item(_InviteSelect(bot, invites))
            if bot.guild_config(guild.id).get("join", {}).get("invites"):
                self.add_item(_LimitInviteSelect(bot, guild))
                self.add_item(_RemoveInviteSelect(bot, guild))
        self.add_item(_BackToSettingsButton(bot))


class RoleCustomizeModal(discord.ui.Modal):
    def __init__(
        self,
        bot: commands.Bot,
        menu: dict,
        role: discord.Role,
        guild: discord.Guild,
        current: dict | None = None,
    ) -> None:
        super().__init__(title=f"Customize {role.name}"[:45])
        self.bot = bot
        self.menu = menu
        self.role = role
        self.guild = guild
        current = current or {}
        emoji_default = current.get("emoji")
        self.emoji_input = discord.ui.TextInput(
            label="Emoji (leave as-is to keep it)",
            required=False,
            placeholder="e.g. 🎮 or <:name:1234>",
            max_length=MAX_EMOJI_LEN,
            default=emoji_default,
        )
        self.label_input = discord.ui.TextInput(
            label="Label",
            required=False,
            placeholder="Shown instead of the role name",
            max_length=80,
            default=current.get("label"),
        )
        self.limit_input = discord.ui.TextInput(
            label="Max members (0 = unlimited)",
            required=False,
            placeholder="0 for no limit",
            max_length=9,
            default=str(current["limit"]) if current.get("limit") else None,
        )
        self.add_item(self.emoji_input)
        self.add_item(self.label_input)
        self.add_item(self.limit_input)

    async def _finish(self, interaction: discord.Interaction, saved: bool) -> None:
        cog = self.bot.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()
        result = None
        if cog is not None and self.menu.get("message_id"):
            result = await cog.refresh_menu(self.menu)
        embed = build_menu_editor_embed(self.bot, self.menu)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        if result is not None and not result[0]:
            embed.add_field(name="Warning", value=result[1], inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu["id"]),
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        emoji = self.emoji_input.value.strip() or None
        label = self.label_input.value.strip() or None
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                f"`{emoji}` doesn't look like a valid emoji. Use a Unicode emoji like 🎮 or a "
                "server emoji like <:name:id>.",
                ephemeral=True,
            )
            return
        try:
            limit = _parse_optional_int(self.limit_input.value)
        except ValueError:
            await interaction.response.send_message("The member limit must be a whole number.", ephemeral=True)
            return

        roles = self.menu.setdefault("roles", [])
        spec = next((item for item in roles if str(item["role_id"]) == str(self.role.id)), None)
        if spec is None:
            spec = {"role_id": str(self.role.id)}
            roles.append(spec)
        if label:
            spec["label"] = label
        else:
            spec.pop("label", None)
        if emoji:
            spec["emoji"] = emoji
        else:
            spec.pop("emoji", None)
        if limit is None:
            spec.pop("limit", None)
        else:
            spec["limit"] = limit

        saved = self.bot.save_config()
        await self._finish(interaction, saved)


class MenuAppearanceModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, menu: dict, guild: discord.Guild) -> None:
        super().__init__(title=f"Appearance · {menu.get('name') or menu['id']}"[:45])
        self.bot = bot
        self.menu = menu
        self.guild = guild
        spec = menu.get("embed") or {}
        color = spec.get("color")
        self.title_input = discord.ui.TextInput(
            label="Title",
            required=False,
            max_length=256,
            default=spec.get("title"),
            placeholder="Text shown above the buttons",
        )
        self.desc_input = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.long,
            required=False,
            max_length=4000,
            default=spec.get("description"),
            placeholder="Inline links: type #rules, @Moderator or @alice",
        )
        self.color_input = discord.ui.TextInput(
            label="Color (#RRGGBB)",
            required=False,
            max_length=9,
            default=f"#{color:06x}" if isinstance(color, int) else None,
        )
        self.footer_input = discord.ui.TextInput(
            label="Footer", required=False, max_length=2048, default=spec.get("footer")
        )
        for item in (self.title_input, self.desc_input, self.color_input, self.footer_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        color_text = self.color_input.value
        try:
            color = _parse_color(color_text) if color_text.strip() else None
        except ValueError:
            await interaction.response.send_message(
                "Color must be like #5865F2 (hex) or a decimal number.", ephemeral=True
            )
            return

        spec = self.menu.setdefault("embed", {})
        spec["title"] = self.title_input.value.strip() or None
        spec["description"] = self.desc_input.value.strip() or None
        spec["footer"] = self.footer_input.value.strip() or None
        if color is not None:
            spec["color"] = color

        saved = self.bot.save_config()
        cog = self.bot.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()
        result = None
        if cog is not None and self.menu.get("message_id"):
            result = await cog.refresh_menu(self.menu)
        embed = build_menu_editor_embed(self.bot, self.menu)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        if result is not None and not result[0]:
            embed.add_field(name="Warning", value=result[1], inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu["id"]),
        )
        await interaction.followup.send("Appearance updated and reflected in the posted message.", ephemeral=True)


class MenuNameModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, menu: dict, guild: discord.Guild) -> None:
        super().__init__(title="Rename menu")
        self.bot = bot
        self.menu = menu
        self.guild = guild
        self.name_input = discord.ui.TextInput(label="Name", max_length=80, default=menu.get("name"))
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.name_input.value.strip()
        if not name:
            await interaction.response.send_message("Name can't be empty.", ephemeral=True)
            return
        self.menu["name"] = name
        saved = self.bot.save_config()
        embed = build_menu_editor_embed(self.bot, self.menu)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu["id"]),
        )





class _AddRoleSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, menu: dict) -> None:
        super().__init__(
            placeholder="Add a role or edit its label/emoji",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.bot = bot
        self.menu_id = menu["id"]

    async def callback(self, interaction: discord.Interaction) -> None:
        menu = _get_menu(self.bot, interaction.guild, self.menu_id)
        if menu is None:
            return
        role = self.values[0]
        current = next(
            (item for item in menu.get("roles", []) if str(item["role_id"]) == str(role.id)), None
        )
        await interaction.response.send_modal(
            RoleCustomizeModal(self.bot, menu, role, interaction.guild, current)
        )


class _RemoveRoleSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, menu: dict) -> None:
        guild = bot.get_guild(int(menu["guild_id"])) if menu.get("guild_id") else None
        options = []
        for spec in menu.get("roles", []):
            role = guild.get_role(int(spec["role_id"])) if guild else None
            label = (spec.get("label") or (role.name if role else spec["role_id"]))[:100]
            kwargs: dict = {"label": label, "value": str(spec["role_id"])}
            if is_valid_emoji(spec.get("emoji")):
                kwargs["emoji"] = spec["emoji"]
            try:
                options.append(discord.SelectOption(**kwargs))
            except (discord.InvalidArgument, ValueError):
                options.append(discord.SelectOption(label=label, value=str(spec["role_id"])))
        super().__init__(placeholder="Remove a role", options=options[:25], row=1)
        self.bot = bot
        self.menu_id = menu["id"]

    async def callback(self, interaction: discord.Interaction) -> None:
        menu = _get_menu(self.bot, interaction.guild, self.menu_id)
        if menu is None:
            return
        role_id = self.values[0]
        roles = menu.setdefault("roles", [])
        roles[:] = [item for item in roles if str(item["role_id"]) != role_id]
        saved = self.bot.save_config()
        cog = self.bot.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()
        result = None
        if cog is not None and menu.get("message_id"):
            result = await cog.refresh_menu(menu)
        embed = build_menu_editor_embed(self.bot, menu)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        if result is not None and not result[0]:
            embed.add_field(name="Warning", value=result[1], inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, interaction.guild, self.menu_id),
        )
        await interaction.followup.send("Role removed and posted menu refreshed.", ephemeral=True)


class MenuEditorView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, menu_id: str) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.menu_id = menu_id
        menu = _get_menu(bot, guild, menu_id)
        if menu is not None:
            self.add_item(_AddRoleSelect(bot, menu))
            if menu.get("roles"):
                self.add_item(_RemoveRoleSelect(bot, menu))

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Move this menu to a channel",
        min_values=1,
        max_values=1,
        row=2,
    )
    async def move_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        cog = self.bot.get_cog("ReactionRoles")
        ok, msg = True, "moved"
        if cog is not None:
            ok, msg = await cog.move_menu(menu, select.values[0].id)
        embed = build_menu_editor_embed(self.bot, menu)
        if not ok:
            embed.add_field(name="Warning", value=msg, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu_id),
        )
        await interaction.followup.send(msg.capitalize(), ephemeral=True)

    @discord.ui.button(label="Post / Refresh", style=discord.ButtonStyle.primary, row=3)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        cog = self.bot.get_cog("ReactionRoles")
        ok = True
        msg = "no menu found"
        if cog is not None:
            ok, msg = await cog.refresh_menu(menu)
        embed = build_menu_editor_embed(self.bot, menu)
        if not ok:
            embed.add_field(name="Warning", value=msg, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu_id),
        )
        await interaction.followup.send(msg.capitalize(), ephemeral=True)

    @discord.ui.button(label="Appearance", style=discord.ButtonStyle.secondary, row=3)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        await interaction.response.send_modal(MenuAppearanceModal(self.bot, menu, self.guild))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, row=3)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        await interaction.response.send_modal(MenuNameModal(self.bot, menu, self.guild))

    @discord.ui.button(label="Unpost", style=discord.ButtonStyle.secondary, row=3)
    async def unpost(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        cog = self.bot.get_cog("ReactionRoles")
        if cog is not None:
            await cog.unpost_menu(menu)
        embed = build_menu_editor_embed(self.bot, menu)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, self.menu_id),
        )
        await interaction.followup.send("Message deleted and menu unposted.", ephemeral=True)

    @discord.ui.button(label="Delete menu", style=discord.ButtonStyle.danger, row=4)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        embed = build_menu_editor_embed(self.bot, menu)
        embed.title = "Delete this menu?"
        embed.description = (
            f"Really delete **{menu.get('name') or menu['id']}** and its posted message? "
            "This can't be undone."
        )
        await interaction.response.edit_message(
            embed=embed,
            view=DeleteMenuConfirmView(self.bot, self.guild, self.menu_id),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_menus_embed(self.bot, self.guild),
            view=MenuPanelView(self.bot, self.guild),
        )


class DeleteMenuConfirmView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, menu_id: str) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.menu_id = menu_id

    @discord.ui.button(label="Confirm delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is not None:
            cog = self.bot.get_cog("ReactionRoles")
            if cog is not None:
                await cog.delete_menu(menu)
        embed = build_menus_embed(self.bot, self.guild)
        embed.description = "Menu deleted."
        await interaction.response.edit_message(
            embed=embed,
            view=MenuPanelView(self.bot, self.guild),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        menu = _get_menu(self.bot, self.guild, self.menu_id)
        if menu is None:
            return
        await interaction.response.edit_message(
            embed=build_menu_editor_embed(self.bot, menu),
            view=MenuEditorView(self.bot, self.guild, self.menu_id),
        )


class _MenuSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        options = []
        for menu in bot.guild_config(guild.id).get("role_menus", []):
            name = menu.get("name") or menu["id"]
            role_count = len(menu.get("roles", []))
            posted = "posted" if menu.get("message_id") else "not posted"
            label = f"{name} · {role_count} roles · {posted}"[:100]
            options.append(discord.SelectOption(label=label, value=menu["id"]))
        super().__init__(placeholder="Manage a role menu", options=options[:25])
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        menu = _get_menu(self.bot, self.guild, self.values[0])
        if menu is None:
            return
        await interaction.response.edit_message(
            embed=build_menu_editor_embed(self.bot, menu),
            view=MenuEditorView(self.bot, self.guild, menu["id"]),
        )


class MenuPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        menus = bot.guild_config(guild.id).get("role_menus", [])
        if menus:
            self.add_item(_MenuSelect(bot, guild))

    @discord.ui.button(label="New menu", style=discord.ButtonStyle.primary)
    async def new_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="New role menu",
                description="Choose the channel for this menu. You can change it later.",
                color=0x5865F2,
            ),
            view=NewMenuView(self.bot, self.guild),
        )

    @discord.ui.button(label="Repost / refresh all", style=discord.ButtonStyle.secondary)
    async def repost_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = self.bot.get_cog("ReactionRoles")
        posted = failed = 0
        if cog is not None:
            posted, failed = await cog.refresh_all(self.guild)
        embed = build_menus_embed(self.bot, self.guild)
        embed.add_field(name="Result", value=f"{posted} posted/refreshed, {failed} failed", inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuPanelView(self.bot, self.guild),
        )
        await interaction.followup.send(f"{posted} posted/refreshed, {failed} failed.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_settings_embed(self.bot, self.guild),
            view=SettingsPanelView(self.bot, self.guild),
        )


class NewMenuView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Choose the channel for this menu",
        min_values=1,
        max_values=1,
    )
    async def channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        menus = self.bot.guild_config(self.guild.id).setdefault("role_menus", [])
        used = {menu["id"] for menu in menus}
        n = 1
        while f"menu{n}" in used:
            n += 1
        menu_id = f"menu{n}"
        menu = {
            "id": menu_id,
            "guild_id": str(self.guild.id),
            "name": f"Menu {n}",
            "channel_id": str(select.values[0].id),
            "message_id": None,
            "embed": {
                "title": "Pick your roles!",
                "description": "Pick the roles you want below.",
                "color": 0x5865F2,
                "footer": "",
            },
            "roles": [],
        }
        menus.append(menu)
        saved = self.bot.save_config()
        cog = self.bot.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()
        embed = build_menu_editor_embed(self.bot, menu)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=MenuEditorView(self.bot, self.guild, menu_id),
        )
        await interaction.followup.send("Menu created — add roles to it now.", ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_menus_embed(self.bot, self.guild),
            view=MenuPanelView(self.bot, self.guild),
        )


class ClearPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=900)
        self.bot = bot

    async def _after_clear(self, interaction: discord.Interaction) -> None:
        saved = self.bot.save_config()
        embed = build_clear_embed(self.bot, interaction.guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Clear kick", style=discord.ButtonStyle.danger, row=0)
    async def clear_kick(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(interaction.guild.id)["kick_channel_id"] = None
        await self._after_clear(interaction)

    @discord.ui.button(label="Clear log", style=discord.ButtonStyle.danger, row=0)
    async def clear_log(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(interaction.guild.id)["log_channel_id"] = None
        await self._after_clear(interaction)

    @discord.ui.button(label="Clear default", style=discord.ButtonStyle.danger, row=1)
    async def clear_default(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(interaction.guild.id).setdefault("join", {})["base_role_id"] = None
        await self._after_clear(interaction)

    @discord.ui.button(label="Clear exempt roles", style=discord.ButtonStyle.danger, row=1)
    async def clear_exempt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(interaction.guild.id)["kick_exempt_roles"] = []
        await self._after_clear(interaction)

    @discord.ui.button(label="Clear admin roles", style=discord.ButtonStyle.danger, row=1)
    async def clear_admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(interaction.guild.id)["admin_roles"] = []
        await self._after_clear(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_settings_embed(self.bot, interaction.guild),
            view=SettingsPanelView(self.bot, interaction.guild),
        )


class _EasterEggsToggleButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        enabled = bool((bot.guild_config(guild.id).get("easter_eggs") or {}).get("enabled", True))
        super().__init__(
            label=f"Easter eggs: {'on' if enabled else 'off'}",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            row=3,
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        easter_eggs = self.bot.guild_config(interaction.guild.id).setdefault("easter_eggs", {})
        easter_eggs["enabled"] = not bool(easter_eggs.get("enabled", True))
        saved = self.bot.save_config()
        embed = build_settings_embed(self.bot, interaction.guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(embed=embed, view=SettingsPanelView(self.bot, interaction.guild))


class SettingsPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.add_item(_EasterEggsToggleButton(bot, guild))

    async def _refresh(self, interaction: discord.Interaction, saved: bool = True) -> None:
        embed = build_settings_embed(self.bot, interaction.guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Set the kick channel",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def kick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        self.bot.guild_config(interaction.guild.id)["kick_channel_id"] = str(select.values[0].id)
        saved = self.bot.save_config()
        await self._refresh(interaction, saved)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Set the admin log channel",
        min_values=1,
        max_values=1,
        row=1,
    )
    async def log_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        self.bot.guild_config(interaction.guild.id)["log_channel_id"] = str(select.values[0].id)
        saved = self.bot.save_config()
        await self._refresh(interaction, saved)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Set the default role on join",
        min_values=1,
        max_values=1,
        row=2,
    )
    async def default_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        self.bot.guild_config(interaction.guild.id).setdefault("join", {})["base_role_id"] = str(select.values[0].id)
        saved = self.bot.save_config()
        await self._refresh(interaction, saved)

    @discord.ui.button(label="Role menus", style=discord.ButtonStyle.secondary, row=3)
    async def role_menus(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_menus_embed(self.bot, interaction.guild),
            view=MenuPanelView(self.bot, interaction.guild),
        )

    @discord.ui.button(label="Invite roles", style=discord.ButtonStyle.secondary, row=3)
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        invites = await _fetch_invites(interaction.guild)
        await interaction.response.edit_message(
            embed=build_invites_embed(self.bot, interaction.guild),
            view=InviteRolesView(self.bot, invites, interaction.guild),
        )

    @discord.ui.button(label="Role permissions", style=discord.ButtonStyle.secondary, row=3)
    async def role_permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_roles_panel_embed(self.bot, interaction.guild),
            view=RolesPanelView(self.bot, interaction.guild),
        )

    @discord.ui.button(label="Clear...", style=discord.ButtonStyle.danger, row=4)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_clear_embed(self.bot, interaction.guild),
            view=ClearPanelView(self.bot),
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Settings closed. Run `/settings` again anytime.", embed=None, view=None
        )


class _ExemptRolesSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        default = _resolved_roles(bot, guild, "kick_exempt_roles")
        super().__init__(
            placeholder="Set roles exempt from the channel kick",
            min_values=0,
            max_values=25,
            row=0,
            default_values=default,
        )
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        self.bot.guild_config(self.guild.id)["kick_exempt_roles"] = [str(role.id) for role in self.values]
        await _refresh_roles_panel(interaction, self.bot, self.guild, self.bot.save_config())


class _AdminRolesSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        default = _resolved_roles(bot, guild, "admin_roles")
        super().__init__(
            placeholder="Set roles that can administrate the bot",
            min_values=0,
            max_values=25,
            row=1,
            default_values=default,
        )
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        self.bot.guild_config(self.guild.id)["admin_roles"] = [str(role.id) for role in self.values]
        await _refresh_roles_panel(interaction, self.bot, self.guild, self.bot.save_config())


def _resolved_roles(bot: commands.Bot, guild: discord.Guild, key: str) -> list[discord.Role]:
    roles = []
    for role_id in bot.guild_config(guild.id).get(key, []):
        role = guild.get_role(int(role_id))
        if role is not None:
            roles.append(role)
    return roles


async def _refresh_roles_panel(
    interaction: discord.Interaction, bot: commands.Bot, guild: discord.Guild, saved: bool
) -> None:
    embed = build_roles_panel_embed(bot, guild)
    if not saved:
        embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
    await interaction.response.edit_message(embed=embed, view=RolesPanelView(bot, guild))


class RolesPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.add_item(_ExemptRolesSelect(bot, guild))
        self.add_item(_AdminRolesSelect(bot, guild))

    @discord.ui.button(label="Remove all exempt", style=discord.ButtonStyle.danger, row=2)
    async def remove_exempt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(self.guild.id)["kick_exempt_roles"] = []
        await _refresh_roles_panel(interaction, self.bot, self.guild, self.bot.save_config())

    @discord.ui.button(label="Remove all admin", style=discord.ButtonStyle.danger, row=2)
    async def remove_admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.bot.guild_config(self.guild.id)["admin_roles"] = []
        await _refresh_roles_panel(interaction, self.bot, self.guild, self.bot.save_config())

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_settings_embed(self.bot, self.guild),
            view=SettingsPanelView(self.bot, self.guild),
        )


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _is_bot_admin(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        admin_roles = interaction.client.guild_config(interaction.guild.id).get("admin_roles", [])
        return any(member.get_role(int(role_id)) is not None for role_id in admin_roles or [])

    @app_commands.command(name="settings", description="Open the interactive server settings panel")
    @app_commands.guild_only()
    async def settings_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works inside a server.", ephemeral=True)
            return
        if not self._is_bot_admin(interaction):
            await interaction.response.send_message(
                "You need the Administrator permission or a bot admin role to use this command.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=build_settings_embed(self.bot, interaction.guild),
            view=SettingsPanelView(self.bot, interaction.guild),
            ephemeral=True,
        )

    @settings_cmd.error
    async def settings_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "You need the Administrator permission or a bot admin role to use this command.", ephemeral=True
            )
        else:
            await interaction.response.send_message("Something went wrong, please try again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))