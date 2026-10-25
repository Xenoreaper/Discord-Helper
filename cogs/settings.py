import logging

import discord
from discord import app_commands
from discord.ext import commands
from discord import ui

from cogs.grant import grant_config, role_grant_problem
from cogs.scheduler import is_valid_timezone
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


def _channel_text(bot: commands.Bot, channel_id) -> str:
    if not channel_id:
        return "not set"
    channel = bot.get_channel(int(channel_id))
    return channel.mention if channel else f"`{channel_id}` (not found)"


def _role_text(guild: discord.Guild, role_id) -> str:
    if not role_id:
        return "not set"
    role = guild.get_role(int(role_id))
    return role.mention if role else f"`{role_id}` (not found)"


def build_settings_view(bot: commands.Bot, guild: discord.Guild) -> ui.LayoutView:
    config = bot.guild_config(guild.id)
    join = config.get("join", {})
    container = ui.Container()
    container.add_item(ui.TextDisplay("# Server Settings"))
    
    # Kick channel
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**No-Chat channel**\n{_channel_text(bot, config.get('kick_channel_id'))}"),
            accessory=ui.Button(label="Set", style=discord.ButtonStyle.secondary, custom_id=f"settings:kick:{guild.id}"),
        )
    )
    # Kick action
    action = "ban" if (config.get("kick_action") or "kick") == "ban" else "kick"
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**No-chat punishment**\n{action}"),
            accessory=ui.Button(label="Toggle", style=discord.ButtonStyle.secondary, custom_id=f"settings:kickaction:{guild.id}"),
        )
    )
    # Log channel
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Log channel**\n{_channel_text(bot, config.get('log_channel_id'))}"),
            accessory=ui.Button(label="Set", style=discord.ButtonStyle.secondary, custom_id=f"settings:log:{guild.id}"),
        )
    )
    # Bot admin roles
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Bot admin roles**\n{_role_list_text(bot, guild, 'admin_roles')}"),
            accessory=ui.Button(label="Manage", style=discord.ButtonStyle.secondary, custom_id=f"settings:adminroles:{guild.id}"),
        )
    )
    # Kick exempt roles
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Kick-exempt roles**\n{_exempt_roles_text(bot, guild)}"),
            accessory=ui.Button(label="Manage", style=discord.ButtonStyle.secondary, custom_id=f"settings:exempt:{guild.id}"),
        )
    )
    # Default role
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Default role (on join)**\n{_role_text(guild, join.get('base_role_id'))}"),
            accessory=ui.Button(label="Set", style=discord.ButtonStyle.secondary, custom_id=f"settings:defrole:{guild.id}"),
        )
    )
    # Invite roles
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Invite roles**\n{_invite_text(bot, guild)}"),
            accessory=ui.Button(label="Manage", style=discord.ButtonStyle.secondary, custom_id=f"settings:invites:{guild.id}"),
        )
    )
    # Grant permissions
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Grant permissions**\n{_grant_summary_text(bot, guild)}"),
            accessory=ui.Button(label="Manage", style=discord.ButtonStyle.secondary, custom_id=f"settings:grant:{guild.id}"),
        )
    )
    # Timezone
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Timezone**\n{config.get('timezone') or 'UTC'}"),
            accessory=ui.Button(label="Set", style=discord.ButtonStyle.secondary, custom_id=f"settings:tz:{guild.id}"),
        )
    )
    # Scheduled messages
    schedules = config.get("scheduled_messages", [])
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Scheduled messages**\n{len(schedules)} configured" if schedules else "**Scheduled messages**\nnone — create one with `/schedule create`"),
            accessory=ui.Button(label="Info", style=discord.ButtonStyle.secondary, custom_id=f"settings:sched:{guild.id}", disabled=True),
        )
    )
    # Role menus
    menus = config.get("role_menus", [])
    if menus:
        lines = []
        for menu in menus:
            channel = guild.get_channel(int(menu["channel_id"])) if menu.get("channel_id") else None
            name = menu.get("name") or menu["id"]
            posted = "posted" if menu.get("message_id") else "not posted"
            lines.append(f"{name} — {channel.mention if channel else 'no channel'} ({len(menu.get('roles', []))} roles, {posted})")
        val = "\n".join(lines)
    else:
        val = "none — create one in the Role menus panel"
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Role menus**\n{val}"),
            accessory=ui.Button(label="Manage", style=discord.ButtonStyle.secondary, custom_id=f"settings:menus:{guild.id}"),
        )
    )
    # Easter eggs
    easter_eggs = config.get("easter_eggs", {})
    easter_enabled = easter_eggs.get("enabled", True)
    container.add_item(
        ui.Section(
            ui.TextDisplay(f"**Easter eggs**\n{'on' if easter_enabled else 'off'}"),
            accessory=ui.Button(label="Toggle", style=discord.ButtonStyle.secondary, custom_id=f"settings:easter:{guild.id}"),
        )
    )
    container.add_item(ui.TextDisplay("Manage role menus in the dedicated panel. Dismiss this message when done."))
    
    view = ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


# Keep old for compatibility
build_settings_embed = build_settings_view


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


def build_menus_view(bot: commands.Bot, guild: discord.Guild) -> discord.ui.LayoutView:
    menus = bot.guild_config(guild.id).get("role_menus", [])
    view = discord.ui.LayoutView(timeout=None)
    container = ui.Container()
    container.add_item(ui.TextDisplay("# Role menus"))
    container.add_item(ui.TextDisplay("Menus are only posted here — nothing gets posted by accident."))
    if not menus:
        container.add_item(ui.TextDisplay("**No menus yet**\nPress **New menu** to create your first one."))
    else:
        for menu in menus:
            channel = guild.get_channel(int(menu["channel_id"])) if menu.get("channel_id") else None
            name = menu.get("name") or menu["id"]
            role_count = len(menu.get("roles", []))
            link = _menu_post_link(bot, menu)
            status = f"[Posted message]({link})" if link else "Not posted yet"
            val = f"{channel.mention if channel else 'no channel set'} · {role_count} role{'s' if role_count != 1 else ''}\n{status}"
            container.add_item(ui.TextDisplay(f"**{name}**\n{val}"))
        row_m = ui.ActionRow()
        msel = discord.ui.Select(
            placeholder="Manage an existing menu",
            options=[
                discord.SelectOption(label=((m.get("name") or m["id"])[:100]), value=m["id"]) for m in menus[:25]
            ],
        )
        async def _open_m(i: discord.Interaction):
            try:
                if not i.response.is_done():
                    try:
                        await i.response.defer()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                menu_id = msel.values[0]
                menu = _get_menu(bot, i.guild, menu_id)
                if menu is None:
                    try:
                        if i.response.is_done():
                            try:
                                await i.edit_original_response(view=build_menus_view(bot, i.guild))
                            except Exception:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=build_menus_view(bot, i.guild))
                        else:
                            await i.response.edit_message(view=build_menus_view(bot, i.guild))
                    except Exception:
                        pass
                    return
                embed = build_menu_editor_embed(bot, menu)
                view_e = MenuEditorView(bot, i.guild, menu_id)
                try:
                    bot.add_view(view_e)
                except Exception:
                    pass
                try:
                    if not i.response.is_done():
                        await i.response.edit_message(embed=embed, view=view_e)
                    else:
                        try:
                            await i.edit_original_response(embed=embed, view=view_e)
                        except Exception:
                            if i.message:
                                await i.followup.edit_message(i.message.id, embed=embed, view=view_e)
                            else:
                                await i.followup.send(embed=embed, view=view_e, ephemeral=True)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                if not i.response.is_done():
                    await i.response.defer()
            except Exception:
                pass
            menu_id = msel.values[0]
            menu = _get_menu(bot, i.guild, menu_id)
            if menu is None:
                try:
                    await i.edit_original_response(view=build_menus_view(bot, i.guild))
                except Exception:
                    try:
                        await i.followup.edit_message(i.message.id, view=build_menus_view(bot, i.guild)) if i.message else None
                    except Exception:
                        pass
                return
            try:
                embed = build_menu_editor_embed(bot, menu)
                view_e = MenuEditorView(bot, i.guild, menu_id)
                try:
                    bot.add_view(view_e)
                except Exception:
                    pass
                if not i.response.is_done():
                    await i.response.edit_message(embed=embed, view=view_e)
                else:
                    try:
                        await i.edit_original_response(embed=embed, view=view_e)
                    except Exception:
                        try:
                            if i.message:
                                await i.followup.edit_message(i.message.id, embed=embed, view=view_e)
                            else:
                                await i.followup.send(embed=embed, view=view_e, ephemeral=True)
                        except Exception:
                            pass
            except Exception:
                pass
        msel.callback = _open_m
        row_m.add_item(msel)
        container.add_item(row_m)
    row = ui.ActionRow()
    new_btn = ui.Button(label="New menu", style=discord.ButtonStyle.primary)
    async def _new(i: discord.Interaction):
        try:
            cog = bot.get_cog("ReactionRoles")
            if cog is not None and hasattr(cog, "create_menu"):
                channel = i.channel
                if isinstance(channel, discord.TextChannel):
                    try:
                        menu = await cog.create_menu(channel)
                        await i.response.edit_message(
                            embed=build_menu_editor_embed(bot, menu),
                            view=MenuEditorView(bot, i.guild, menu["id"]),
                        )
                        return
                    except Exception:
                        pass
            await i.response.edit_message(
                embed=discord.Embed(
                    title="New role menu",
                    description="Choose the channel for this menu. You can change it later.",
                    color=0x5865F2,
                ),
                view=NewMenuView(bot, guild),
            )
        except Exception:
            try:
                await i.response.edit_message(embed=build_menu_creator_embed(bot, guild), view=MenuCreatorView(bot, guild))
            except Exception:
                pass
    new_btn.callback = _new
    rep_btn = ui.Button(label="Repost/refresh all", style=discord.ButtonStyle.secondary)
    async def _rep(i: discord.Interaction):
        try:
            cog = bot.get_cog("ReactionRoles")
            if cog is not None:
                try:
                    await cog.refresh_all(i.guild)
                except Exception:
                    for m in bot.guild_config(i.guild.id).get("role_menus", []):
                        try:
                            await cog.refresh_menu(m)
                        except Exception:
                            pass
            await i.response.edit_message(view=build_menus_view(bot, i.guild))
        except Exception:
            try:
                await i.response.edit_message(embed=build_menus_embed(bot, guild), view=MenuPanelView(bot, guild))
            except Exception:
                pass
    rep_btn.callback = _rep
    row.add_item(new_btn)
    row.add_item(rep_btn)
    container.add_item(row)
    back_row = ui.ActionRow()
    back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
    async def _back(i: discord.Interaction):
        try:
            v = build_settings_view(bot, i.guild)
            try:
                bot.add_view(v)
            except Exception:
                pass
            if not i.response.is_done():
                await i.response.edit_message(view=v)
            else:
                try:
                    await i.edit_original_response(view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, view=v)
                        else:
                            await i.followup.send(view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    back_btn.callback = _back
    back_row.add_item(back_btn)
    container.add_item(back_row)
    view.add_item(container)
    try:
        bot.add_view(view)
    except Exception:
        pass
    return view


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


def build_invites_view(bot: commands.Bot, invites: list[discord.Invite], guild: discord.Guild, pending_invite: str | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = ui.Container()
    container.add_item(ui.TextDisplay("# Invite roles"))
    container.add_item(ui.TextDisplay("Map an invite link to a role. Members who join with that invite get the role."))
    container.add_item(ui.TextDisplay(f"**Configured**\n{_invite_text(bot, guild)}"))
    if pending_invite:
        container.add_item(ui.TextDisplay(f"**Pending**\nNow pick the role for invite `{pending_invite}`."))
        row = ui.ActionRow()
        row.add_item(_RoleForInviteSelect(bot, pending_invite, guild, invites))
        container.add_item(row)
    else:
        if invites:
            row1 = ui.ActionRow()
            row1.add_item(_InviteSelect(bot, invites, guild))
            container.add_item(row1)
        if bot.guild_config(guild.id).get("join", {}).get("invites"):
            row2 = ui.ActionRow()
            row2.add_item(_LimitInviteSelect(bot, guild, invites))
            container.add_item(row2)
            row3 = ui.ActionRow()
            row3.add_item(_RemoveInviteSelect(bot, guild, invites))
            container.add_item(row3)
    back_row = ui.ActionRow()
    back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
    async def _back(i: discord.Interaction):
        try:
            v = build_settings_view(bot, i.guild)
            try:
                bot.add_view(v)
            except Exception:
                pass
            if not i.response.is_done():
                await i.response.edit_message(view=v)
            else:
                try:
                    await i.edit_original_response(view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, view=v)
                        else:
                            await i.followup.send(view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    back_btn.callback = _back
    back_row.add_item(back_btn)
    container.add_item(back_row)
    view.add_item(container)
    try:
        bot.add_view(view)
    except Exception:
        pass
    return view


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


def build_roles_panel_view(bot: commands.Bot, guild: discord.Guild) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = ui.Container()
    container.add_item(ui.TextDisplay("# Member & admin roles"))
    container.add_item(ui.TextDisplay("Select roles to add them — current roles are pre-selected, so deselect to remove specific ones. Admin roles can open /settings and bypass the channel kick; kick-exempt roles only bypass the kick."))
    row1 = ui.ActionRow()
    row1.add_item(_ExemptRolesSelect(bot, guild))
    container.add_item(row1)
    row2 = ui.ActionRow()
    row2.add_item(_AdminRolesSelect(bot, guild))
    container.add_item(row2)
    back_row = ui.ActionRow()
    back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
    async def _back(i: discord.Interaction):
        try:
            v = build_settings_view(bot, i.guild)
            try:
                bot.add_view(v)
            except Exception:
                pass
            if not i.response.is_done():
                await i.response.edit_message(view=v)
            else:
                try:
                    await i.edit_original_response(view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, view=v)
                        else:
                            await i.followup.send(view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    back_btn.callback = _back
    back_row.add_item(back_btn)
    container.add_item(back_row)
    view.add_item(container)
    try:
        bot.add_view(view)
    except Exception:
        pass
    return view


def _grant_summary_text(bot: commands.Bot, guild: discord.Guild) -> str:
    grant = bot.guild_config(guild.id).get("grant_permissions", {})
    users = len(grant.get("users", {}))
    roles = len(grant.get("roles", {}))
    if not users and not roles:
        return "none — only administrators and bot admin roles can grant roles"
    parts = []
    if users:
        parts.append(f"{users} member{'s' if users != 1 else ''}")
    if roles:
        parts.append(f"{roles} role{'s' if roles != 1 else ''}")
    return f"{', '.join(parts)} with custom grants"


def _grant_subject_lines(bot: commands.Bot, guild: discord.Guild, bucket: str) -> str:
    entries = bot.guild_config(guild.id).get("grant_permissions", {}).get(bucket, {})
    lines = []
    for subject_id, role_ids in entries.items():
        if bucket == "users":
            subject = guild.get_member(int(subject_id))
        else:
            subject = guild.get_role(int(subject_id))
        label = subject.mention if subject else f"`{subject_id}` (not found)"
        roles = []
        for role_id in role_ids:
            role = guild.get_role(int(role_id))
            roles.append(role.mention if role else f"`{role_id}` (not found)")
        lines.append(f"{label} → {', '.join(roles) or 'none'}")
    return "\n".join(lines)[:1024] or "none"


def build_grant_permissions_view(bot: commands.Bot, guild: discord.Guild) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = ui.Container()
    container.add_item(ui.TextDisplay("# Grant permissions"))
    container.add_item(ui.TextDisplay("Let specific members or roles grant roles using /grant."))
    container.add_item(ui.TextDisplay(f"**Users**\n{_grant_subject_lines(bot, guild, 'users')}"))
    container.add_item(ui.TextDisplay(f"**Roles**\n{_grant_subject_lines(bot, guild, 'roles')}"))
    row = ui.ActionRow()
    msel = discord.ui.MemberSelect(placeholder="Add/edit user", min_values=1, max_values=1)
    async def _mu(i: discord.Interaction, s: discord.ui.MemberSelect):
        try:
            member = s.values[0]
            v = GrantPermissionsView(bot, guild, "user", str(member.id))
            embed = build_grant_permissions_embed(bot, guild, member, "user")
            if not i.response.is_done():
                await i.response.edit_message(embed=embed, view=v)
            else:
                try:
                    await i.edit_original_response(embed=embed, view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, embed=embed, view=v)
                        else:
                            await i.followup.send(embed=embed, view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    msel.callback = _mu
    row.add_item(msel)
    container.add_item(row)
    row2 = ui.ActionRow()
    rsel = discord.ui.RoleSelect(placeholder="Add/edit role", min_values=1, max_values=1)
    async def _mr(i: discord.Interaction, s: discord.ui.RoleSelect):
        try:
            role = s.values[0]
            v = GrantPermissionsView(bot, guild, "role", str(role.id))
            embed = build_grant_permissions_embed(bot, guild, role, "role")
            if not i.response.is_done():
                await i.response.edit_message(embed=embed, view=v)
            else:
                try:
                    await i.edit_original_response(embed=embed, view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, embed=embed, view=v)
                        else:
                            await i.followup.send(embed=embed, view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    rsel.callback = _mr
    row2.add_item(rsel)
    container.add_item(row2)
    back_row = ui.ActionRow()
    back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
    async def _back(i: discord.Interaction):
        try:
            v = build_settings_view(bot, i.guild)
            try:
                bot.add_view(v)
            except Exception:
                pass
            if not i.response.is_done():
                await i.response.edit_message(view=v)
            else:
                try:
                    await i.edit_original_response(view=v)
                except Exception:
                    try:
                        if i.message:
                            await i.followup.edit_message(i.message.id, view=v)
                        else:
                            await i.followup.send(view=v, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            pass
    back_btn.callback = _back
    back_row.add_item(back_btn)
    container.add_item(back_row)
    view.add_item(container)
    try:
        bot.add_view(view)
    except Exception:
        pass
    return view


def build_grant_permissions_embed(
    bot: commands.Bot,
    guild: discord.Guild,
    subject: discord.Member | discord.Role | None = None,
    subject_kind: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="Grant permissions",
        description=(
            "Who may use `/grant` to hand out which roles. Administrators and bot admin roles "
            "don't need an entry — they can grant any role below their own highest role. Pick a "
            "member or a role below, then choose the roles they are allowed to give."
        ),
        color=0x5865F2,
    )
    embed.add_field(name="Members", value=_grant_subject_lines(bot, guild, "users"), inline=False)
    embed.add_field(name="Roles", value=_grant_subject_lines(bot, guild, "roles"), inline=False)
    if subject is not None:
        kind = "member" if subject_kind == "user" else "role"
        embed.add_field(
            name="Editing",
            value=f"{subject.mention} ({kind}) — select the roles below and submit (submit empty to remove).",
            inline=False,
        )
    embed.set_footer(text="Everyone can only grant roles below their own highest role.")
    return embed


def _resolve_grant_subject(
    bot: commands.Bot, guild: discord.Guild, subject_kind: str | None, subject_id: str | None
) -> tuple[discord.Member | discord.Role | None, discord.Role | None]:
    if subject_kind == "user" and subject_id:
        member = guild.get_member(int(subject_id))
        if member is not None:
            return member, member.top_role
    if subject_kind == "role" and subject_id:
        role = guild.get_role(int(subject_id))
        if role is not None:
            return role, role
    return None, None


async def _fetch_invites(guild: discord.Guild) -> list[discord.Invite]:
    try:
        return await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.warning("Could not fetch invites for %s", guild.name, exc_info=True)
        return []


class _InviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, invites: list[discord.Invite], guild: discord.Guild) -> None:
        options = []
        for invite in invites[:25]:
            inviter = invite.inviter
            label = f"{inviter} • {invite.uses} uses • {invite.code}" if inviter else f"{invite.uses} uses • {invite.code}"
            options.append(discord.SelectOption(label=label[:100], value=invite.code))
        super().__init__(placeholder="Select an invite to map to a role", options=options)
        self.bot = bot
        self.invites = invites
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            view = build_invites_view(self.bot, self.invites, self.guild, pending_invite=self.values[0])
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    try:
                        if interaction.message:
                            await interaction.followup.edit_message(interaction.message.id, view=view)
                        else:
                            await interaction.followup.send(view=view, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            await interaction.response.edit_message(
                embed=build_invites_embed(self.bot, self.guild),
                view=InviteRolesView(self.bot, self.invites, self.guild, pending_invite=self.values[0]),
            )


class _RoleForInviteSelect(discord.ui.RoleSelect):
    def __init__(
        self, bot: commands.Bot, code: str, guild: discord.Guild, invites: list[discord.Invite]
    ) -> None:
        super().__init__(placeholder=f"Pick the role for invite {code}", min_values=1, max_values=1)
        self.bot = bot
        self.code = code
        self.guild = guild
        self.invites = invites

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        self.bot.guild_config(self.guild.id).setdefault("join", {}).setdefault("invites", {})[
            self.code
        ] = str(role.id)
        saved = self.bot.save_config()
        try:
            view = build_invites_view(self.bot, self.invites, self.guild)
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    try:
                        if interaction.message:
                            await interaction.followup.edit_message(interaction.message.id, view=view)
                        else:
                            await interaction.followup.send(view=view, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            await interaction.response.edit_message(
                embed=build_invites_embed(self.bot, self.guild),
                view=InviteRolesView(self.bot, self.invites, self.guild),
            )
        if not saved:
            try:
                await interaction.followup.send(_SAVE_FAILED, ephemeral=True)
            except Exception:
                pass


class _RemoveInviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, invites: list[discord.Invite]) -> None:
        options = []
        for code, spec in bot.guild_config(guild.id).get("join", {}).get("invites", {}).items():
            role_id = spec if isinstance(spec, str) else spec.get("role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            label = f"{code} -> {role.name if role else role_id}"
            options.append(discord.SelectOption(label=label[:100], value=code))
        super().__init__(placeholder="Remove an invite mapping", options=options[:25])
        self.bot = bot
        self.guild = guild
        self.invites = invites

    async def callback(self, interaction: discord.Interaction) -> None:
        code = self.values[0]
        self.bot.guild_config(self.guild.id).get("join", {}).get("invites", {}).pop(code, None)
        saved = self.bot.save_config()
        try:
            view = build_invites_view(self.bot, self.invites, self.guild)
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    try:
                        if interaction.message:
                            await interaction.followup.edit_message(interaction.message.id, view=view)
                        else:
                            await interaction.followup.send(view=view, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            await interaction.response.edit_message(
                embed=build_invites_embed(self.bot, self.guild),
                view=InviteRolesView(self.bot, self.invites, self.guild),
            )
        if not saved:
            try:
                await interaction.followup.send(_SAVE_FAILED, ephemeral=True)
            except Exception:
                pass


class InviteLimitModal(discord.ui.Modal):
    def __init__(
        self, bot: commands.Bot, guild: discord.Guild, code: str, invites: list[discord.Invite]
    ) -> None:
        super().__init__(title=f"Limits for invite {code}"[:45])
        self.bot = bot
        self.guild = guild
        self.code = code
        self.invites = invites
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

        invites_cfg = self.bot.guild_config(self.guild.id).setdefault("join", {}).setdefault("invites", {})
        raw = invites_cfg.get(self.code)
        spec = dict(raw) if isinstance(raw, dict) else {"role_id": raw}
        for field, value in (("max_uses", max_uses), ("within_days", window_days)):
            if value is None:
                spec.pop(field, None)
            else:
                spec[field] = value
        invites_cfg[self.code] = spec

        saved = self.bot.save_config()
        try:
            view = build_invites_view(self.bot, self.invites, self.guild)
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    try:
                        if interaction.message:
                            await interaction.followup.edit_message(interaction.message.id, view=view)
                        else:
                            await interaction.followup.send(view=view, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            await interaction.response.edit_message(
                embed=build_invites_embed(self.bot, self.guild),
                view=InviteRolesView(self.bot, self.invites, self.guild),
            )
        if not saved:
            try:
                await interaction.followup.send(_SAVE_FAILED, ephemeral=True)
            except Exception:
                pass


class _LimitInviteSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, invites: list[discord.Invite]) -> None:
        options = []
        for code, spec in bot.guild_config(guild.id).get("join", {}).get("invites", {}).items():
            role_id = spec if isinstance(spec, str) else spec.get("role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            label = f"{code} -> {role.name if role else role_id}"
            options.append(discord.SelectOption(label=label[:100], value=code))
        super().__init__(placeholder="Limit an invite's role", options=options[:25])
        self.bot = bot
        self.guild = guild
        self.invites = invites

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_modal(
                InviteLimitModal(self.bot, self.guild, self.values[0], self.invites)
            )
        except Exception:
            try:
                await interaction.followup.send("Use the limit button again.", ephemeral=True)
            except Exception:
                pass


class _BackToSettingsButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            view = build_settings_view(self.bot, interaction.guild)
            try:
                self.bot.add_view(view)
            except Exception:
                pass
            await interaction.response.edit_message(view=view)
        except Exception:
            try:
                await interaction.response.edit_message(
                    embed=build_settings_embed(self.bot, interaction.guild),
                    view=SettingsPanelView(self.bot, interaction.guild),
                )
            except Exception:
                pass


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
            self.add_item(_RoleForInviteSelect(bot, pending_invite, guild, invites))
        else:
            if invites:
                self.add_item(_InviteSelect(bot, invites, guild))
            if bot.guild_config(guild.id).get("join", {}).get("invites"):
                self.add_item(_LimitInviteSelect(bot, guild, invites))
                self.add_item(_RemoveInviteSelect(bot, guild, invites))
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
        try:
            view = build_settings_view(self.bot, self.guild)
            try:
                self.bot.add_view(view)
            except Exception:
                pass
            await interaction.response.edit_message(view=view)
            return
        except Exception:
            pass
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
        try:
            v = build_menus_view(self.bot, self.guild)
            await interaction.response.edit_message(view=v)
            return
        except Exception:
            pass
        try:
            await interaction.response.edit_message(
                embed=build_settings_embed(self.bot, self.guild),
                view=SettingsPanelView(self.bot, self.guild),
            )
        except Exception:
            pass


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
        try:
            view = build_settings_view(self.bot, self.guild)
            try:
                self.bot.add_view(view)
            except Exception:
                pass
            await interaction.response.edit_message(view=view)
            return
        except Exception:
            pass
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


class _NoChatActionToggleButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        is_ban = (bot.guild_config(guild.id).get("kick_action") or "kick") == "ban"
        super().__init__(
            label=f"No-chat: {'ban' if is_ban else 'kick'}",
            style=discord.ButtonStyle.danger if is_ban else discord.ButtonStyle.secondary,
            row=3,
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        config = self.bot.guild_config(interaction.guild.id)
        config["kick_action"] = "kick" if (config.get("kick_action") or "kick") == "ban" else "ban"
        saved = self.bot.save_config()
        embed = build_settings_embed(self.bot, interaction.guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(embed=embed, view=SettingsPanelView(self.bot, interaction.guild))


class TimezoneModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(title="Set timezone")
        self.bot = bot
        self.guild = guild
        current = bot.guild_config(guild.id).get("timezone") or "UTC"
        self.tz_input = discord.ui.TextInput(
            label="Timezone (IANA name)",
            default=current,
            max_length=64,
            placeholder="e.g. Europe/Berlin, America/New_York, UTC",
        )
        self.add_item(self.tz_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.tz_input.value.strip()
        if not name or not is_valid_timezone(name):
            await interaction.response.send_message(
                "That isn't a known timezone. Use an IANA name like `Europe/Berlin` or `UTC`.",
                ephemeral=True,
            )
            return
        self.bot.guild_config(self.guild.id)["timezone"] = name
        saved = self.bot.save_config()
        embed = build_settings_embed(self.bot, self.guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=SettingsPanelView(self.bot, self.guild),
        )
        await interaction.followup.send(f"Timezone set to **{name}**.", ephemeral=True)


class SettingsPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.add_item(_EasterEggsToggleButton(bot, guild))
        self.add_item(_NoChatActionToggleButton(bot, guild))

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

    @discord.ui.button(label="Grant permissions", style=discord.ButtonStyle.secondary, row=4)
    async def grant_permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=build_grant_permissions_embed(self.bot, interaction.guild),
            view=GrantPermissionsView(self.bot, interaction.guild),
        )

    @discord.ui.button(label="Timezone", style=discord.ButtonStyle.secondary, row=4)
    async def timezone(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TimezoneModal(self.bot, interaction.guild))

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
    try:
        view = build_roles_panel_view(bot, guild)
        if not saved:
            pass
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=view)
            else:
                try:
                    await interaction.edit_original_response(view=view)
                except Exception:
                    try:
                        if interaction.message:
                            await interaction.followup.edit_message(interaction.message.id, view=view)
                        else:
                            await interaction.followup.send(view=view, ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            try:
                embed = build_roles_panel_embed(bot, guild)
                if not saved:
                    embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
                await interaction.response.edit_message(embed=embed, view=RolesPanelView(bot, guild))
            except Exception:
                pass
    except Exception:
        embed = build_roles_panel_embed(bot, guild)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        try:
            await interaction.response.edit_message(embed=embed, view=RolesPanelView(bot, guild))
        except Exception:
            pass


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


class _GrantMemberSubjectSelect(discord.ui.UserSelect):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(placeholder="Edit a member's grantable roles", min_values=1, max_values=1, row=0)
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        value = self.values[0]
        member = value if isinstance(value, discord.Member) else self.guild.get_member(value.id)
        if member is None:
            await interaction.response.send_message("That member isn't in this server.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=build_grant_permissions_embed(self.bot, self.guild, member, "user"),
            view=GrantPermissionsView(self.bot, self.guild, "user", str(member.id)),
        )


class _GrantRoleSubjectSelect(discord.ui.RoleSelect):
    def __init__(self, bot: commands.Bot, guild: discord.Guild) -> None:
        super().__init__(placeholder="Edit a role's grantable roles", min_values=1, max_values=1, row=1)
        self.bot = bot
        self.guild = guild

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await interaction.response.edit_message(
            embed=build_grant_permissions_embed(self.bot, self.guild, role, "role"),
            view=GrantPermissionsView(self.bot, self.guild, "role", str(role.id)),
        )


class _GrantRolesSelect(discord.ui.RoleSelect):
    def __init__(
        self, bot: commands.Bot, guild: discord.Guild, subject_kind: str | None, subject_id: str | None
    ) -> None:
        subject, _ = _resolve_grant_subject(bot, guild, subject_kind, subject_id)
        default: list[discord.Role] = []
        if subject is not None:
            bucket = "users" if subject_kind == "user" else "roles"
            for role_id in grant_config(bot, guild.id)[bucket].get(str(subject.id), []):
                role = guild.get_role(int(role_id))
                if role is not None:
                    default.append(role)
        if subject is None:
            placeholder = "Pick a member or role above first"
        else:
            label = subject.display_name if isinstance(subject, discord.Member) else subject.name
            placeholder = f"Roles {label} may grant"
        super().__init__(
            placeholder=placeholder[:150],
            min_values=0,
            max_values=25,
            row=2,
            default_values=default,
        )
        self.bot = bot
        self.guild = guild
        self.subject_kind = subject_kind
        self.subject_id = subject_id

    async def callback(self, interaction: discord.Interaction) -> None:
        subject, top_role = _resolve_grant_subject(self.bot, self.guild, self.subject_kind, self.subject_id)
        if subject is None or top_role is None:
            await interaction.response.send_message("Pick a member or a role above first.", ephemeral=True)
            return

        problems = []
        for role in self.values:
            problem = role_grant_problem(role, top_role)
            if problem is not None:
                problems.append(f"{role.mention} — {problem}")
        if problems:
            await interaction.response.send_message(
                "Nothing was saved. These roles can't be granted by that subject:\n"
                + "\n".join(problems)[:1500],
                ephemeral=True,
            )
            return

        bucket = "users" if self.subject_kind == "user" else "roles"
        entries = grant_config(self.bot, self.guild.id)[bucket]
        if self.values:
            entries[str(subject.id)] = [str(role.id) for role in self.values]
        else:
            entries.pop(str(subject.id), None)
        saved = self.bot.save_config()
        embed = build_grant_permissions_embed(self.bot, self.guild, subject, self.subject_kind)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=GrantPermissionsView(self.bot, self.guild, self.subject_kind, self.subject_id),
        )


class GrantPermissionsView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.bot = bot
        self.guild = guild
        self.subject_kind = subject_kind
        self.subject_id = subject_id
        self.add_item(_GrantMemberSubjectSelect(bot, guild))
        self.add_item(_GrantRoleSubjectSelect(bot, guild))
        self.add_item(_GrantRolesSelect(bot, guild, subject_kind, subject_id))

    @discord.ui.button(label="Remove all for subject", style=discord.ButtonStyle.danger, row=3)
    async def remove_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        subject, _ = _resolve_grant_subject(self.bot, self.guild, self.subject_kind, self.subject_id)
        if subject is None:
            await interaction.response.send_message("Pick a member or a role above first.", ephemeral=True)
            return
        bucket = "users" if self.subject_kind == "user" else "roles"
        grant_config(self.bot, self.guild.id)[bucket].pop(str(subject.id), None)
        saved = self.bot.save_config()
        embed = build_grant_permissions_embed(self.bot, self.guild, subject, self.subject_kind)
        if not saved:
            embed.add_field(name="Warning", value=_SAVE_FAILED, inline=False)
        await interaction.response.edit_message(
            embed=embed,
            view=GrantPermissionsView(self.bot, self.guild, self.subject_kind, self.subject_id),
        )

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
        return interaction.client.is_bot_admin(member)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        try:
            if interaction.type != discord.InteractionType.component:
                return
        except Exception:
            if getattr(interaction, "type", None) != 3:
                return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not self._is_bot_admin(interaction):
            return
        data = getattr(interaction, "data", None) or {}
        custom_id = data.get("custom_id", "") if isinstance(data, dict) else getattr(data, "custom_id", "")
        if not custom_id or not str(custom_id).startswith("settings:"):
            return
        try:
            parts = str(custom_id).split(":")
            if len(parts) < 3:
                return
            action = parts[1]
            guild_id = int(parts[2])
            if interaction.guild.id != guild_id:
                return
        except Exception:
            return
        try:
            if not interaction.response.is_done():
                try:
                    # Try to defer
                    try:
                        await interaction.response.defer()
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

        bot = self.bot
        if action == "kick":
            view = discord.ui.LayoutView(timeout=None)
            container = ui.Container()
            container.add_item(ui.TextDisplay("**Set No-Chat channel**"))
            row = ui.ActionRow()
            ch = ui.ChannelSelect(channel_types=[discord.ChannelType.text], placeholder="Select channel")
            row.add_item(ch)
            container.add_item(row)
            container.add_item(ui.TextDisplay("Pick a channel above or go back."))
            back_row = ui.ActionRow()
            back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
            async def _back_k(i: discord.Interaction):
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    pass
            back_btn.callback = _back_k
            back_row.add_item(back_btn)
            container.add_item(back_row)
            view.add_item(container)
            async def _cb(i: discord.Interaction, select: discord.ui.ChannelSelect):
                bot.guild_config(i.guild.id)["kick_channel_id"] = str(select.values[0].id)
                bot.save_config()
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    try:
                        await i.followup.send("Updated.", ephemeral=True)
                    except Exception:
                        pass
            ch.callback = _cb
            try:
                bot.add_view(view)
            except Exception:
                pass
            try:
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send(view=view, ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "kickaction":
            config = bot.guild_config(interaction.guild.id)
            config["kick_action"] = "kick" if (config.get("kick_action") or "kick") == "ban" else "ban"
            bot.save_config()
            try:
                view = build_settings_view(bot, interaction.guild)
                try:
                    bot.add_view(view)
                except Exception:
                    pass
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
            except Exception:
                try:
                    view = build_settings_view(bot, interaction.guild)
                    await interaction.followup.send(view=view, ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "log":
            view = discord.ui.LayoutView(timeout=None)
            container = ui.Container()
            container.add_item(ui.TextDisplay("**Set Log channel**"))
            row = ui.ActionRow()
            ch = ui.ChannelSelect(channel_types=[discord.ChannelType.text], placeholder="Select channel")
            row.add_item(ch)
            container.add_item(row)
            container.add_item(ui.TextDisplay("Pick a channel above or go back."))
            back_row = ui.ActionRow()
            back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
            async def _back_l(i: discord.Interaction):
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    pass
            back_btn.callback = _back_l
            back_row.add_item(back_btn)
            container.add_item(back_row)
            view.add_item(container)
            async def _cb(i: discord.Interaction, select: discord.ui.ChannelSelect):
                bot.guild_config(i.guild.id)["log_channel_id"] = str(select.values[0].id)
                bot.save_config()
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    try:
                        await i.followup.send("Updated.", ephemeral=True)
                    except Exception:
                        pass
            ch.callback = _cb
            try:
                bot.add_view(view)
            except Exception:
                pass
            try:
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send(view=view, ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "adminroles":
            try:
                view = build_roles_panel_view(bot, interaction.guild)
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
            except Exception:
                try:
                    if interaction.response.is_done():
                        try:
                            await interaction.edit_original_response(embed=build_roles_panel_embed(bot, interaction.guild), view=RolesPanelView(bot, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened roles panel.", ephemeral=True)
                    else:
                        try:
                            await interaction.response.edit_message(embed=build_roles_panel_embed(bot, interaction.guild), view=RolesPanelView(bot, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened roles panel.", ephemeral=True)
                except Exception:
                    try:
                        await interaction.followup.send("Opened roles panel.", ephemeral=True)
                    except Exception:
                        pass
            return
        elif action == "exempt":
            try:
                view = build_roles_panel_view(bot, interaction.guild)
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
            except Exception:
                try:
                    if interaction.response.is_done():
                        try:
                            await interaction.edit_original_response(embed=build_roles_panel_embed(bot, interaction.guild), view=RolesPanelView(bot, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened roles panel.", ephemeral=True)
                    else:
                        try:
                            await interaction.response.edit_message(embed=build_roles_panel_embed(bot, interaction.guild), view=RolesPanelView(bot, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened roles panel.", ephemeral=True)
                except Exception:
                    try:
                        await interaction.followup.send("Opened roles panel.", ephemeral=True)
                    except Exception:
                        pass
            return
        elif action == "defrole":
            view = discord.ui.LayoutView(timeout=None)
            container = ui.Container()
            container.add_item(ui.TextDisplay("**Set default role on join**"))
            row = ui.ActionRow()
            rs = ui.RoleSelect(placeholder="Select role", min_values=1, max_values=1)
            row.add_item(rs)
            container.add_item(row)
            container.add_item(ui.TextDisplay("Pick a role above or go back."))
            back_row = ui.ActionRow()
            back_btn = ui.Button(label="Back", style=discord.ButtonStyle.secondary)
            async def _back_d(i: discord.Interaction):
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    pass
            back_btn.callback = _back_d
            back_row.add_item(back_btn)
            container.add_item(back_row)
            view.add_item(container)
            async def _cb(i: discord.Interaction, select: discord.ui.RoleSelect):
                bot.guild_config(i.guild.id).setdefault("join", {})["base_role_id"] = str(select.values[0].id)
                bot.save_config()
                try:
                    v = build_settings_view(bot, i.guild)
                    try:
                        bot.add_view(v)
                    except Exception:
                        pass
                    if not i.response.is_done():
                        await i.response.edit_message(view=v)
                    else:
                        try:
                            await i.edit_original_response(view=v)
                        except Exception:
                            try:
                                if i.message:
                                    await i.followup.edit_message(i.message.id, view=v)
                                else:
                                    await i.followup.send(view=v, ephemeral=True)
                            except Exception:
                                pass
                except Exception:
                    try:
                        await i.followup.send("Updated.", ephemeral=True)
                    except Exception:
                        pass
            rs.callback = _cb
            try:
                bot.add_view(view)
            except Exception:
                pass
            try:
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send(view=view, ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "invites":
            invites = await _fetch_invites(interaction.guild)
            try:
                view = build_invites_view(bot, invites, interaction.guild)
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            try:
                                await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                pass
            except Exception:
                try:
                    if interaction.response.is_done():
                        try:
                            await interaction.edit_original_response(embed=build_invites_embed(bot, interaction.guild), view=InviteRolesView(bot, invites, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened invites panel.", ephemeral=True)
                    else:
                        try:
                            await interaction.response.edit_message(embed=build_invites_embed(bot, interaction.guild), view=InviteRolesView(bot, invites, interaction.guild))
                        except Exception:
                            await interaction.followup.send("Opened invites panel.", ephemeral=True)
                except Exception:
                    try:
                        await interaction.followup.send("Opened invites panel.", ephemeral=True)
                    except Exception:
                        pass
            return
        elif action == "grant":
            try:
                try:
                    if not interaction.response.is_done():
                        try:
                            await interaction.response.defer()
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    view = build_grant_permissions_view(bot, interaction.guild)
                    if interaction.response.is_done():
                        try:
                            await interaction.edit_original_response(view=view)
                        except Exception:
                            try:
                                if interaction.message:
                                    await interaction.followup.edit_message(interaction.message.id, view=view)
                                else:
                                    await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                try:
                                    await interaction.followup.send(view=view, ephemeral=True)
                                except Exception:
                                    pass
                    else:
                        try:
                            await interaction.response.edit_message(view=view)
                        except Exception:
                            try:
                                if interaction.message:
                                    await interaction.followup.edit_message(interaction.message.id, view=view)
                                else:
                                    await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                try:
                                    await interaction.followup.send(view=view, ephemeral=True)
                                except Exception:
                                    pass
                    return
                except Exception:
                    pass
                # Fallback to old style
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild))
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild))
                            else:
                                await interaction.followup.send(embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild), ephemeral=True)
                        except Exception:
                            await interaction.followup.send("Opened grant panel.", ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild))
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild))
                            else:
                                await interaction.followup.send(embed=build_grant_permissions_embed(bot, interaction.guild), view=GrantPermissionsView(bot, interaction.guild), ephemeral=True)
                        except Exception:
                            await interaction.followup.send("Opened grant panel.", ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Opened grant panel.", ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "tz":
            try:
                await interaction.response.send_modal(TimezoneModal(bot, interaction.guild))
            except Exception:
                try:
                    if interaction.response.is_done():
                        try:
                            await interaction.followup.send("Use /settings again.", ephemeral=True)
                        except Exception:
                            pass
                    else:
                        try:
                            await interaction.response.send_message("Use /settings again.", ephemeral=True)
                        except Exception:
                            pass
                except Exception:
                    pass
            return
        elif action == "menus":
            try:
                try:
                    view = build_menus_view(bot, interaction.guild)
                    if interaction.response.is_done():
                        try:
                            await interaction.edit_original_response(view=view)
                        except Exception:
                            try:
                                if interaction.message:
                                    await interaction.followup.edit_message(interaction.message.id, view=view)
                                else:
                                    await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                try:
                                    await interaction.followup.send(view=view, ephemeral=True)
                                except Exception:
                                    pass
                    else:
                        try:
                            await interaction.response.edit_message(view=view)
                        except Exception:
                            try:
                                if interaction.message:
                                    await interaction.followup.edit_message(interaction.message.id, view=view)
                                else:
                                    await interaction.followup.send(view=view, ephemeral=True)
                            except Exception:
                                try:
                                    await interaction.followup.send(view=view, ephemeral=True)
                                except Exception:
                                    pass
                    return
                except Exception:
                    pass
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild))
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild))
                            else:
                                await interaction.followup.send(embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild), ephemeral=True)
                        except Exception:
                            await interaction.followup.send("Opened menus panel.", ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild))
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild))
                            else:
                                await interaction.followup.send(embed=build_menus_embed(bot, interaction.guild), view=MenuPanelView(bot, interaction.guild), ephemeral=True)
                        except Exception:
                            await interaction.followup.send("Opened menus panel.", ephemeral=True)
            except Exception:
                try:
                    await interaction.followup.send("Opened menus panel.", ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "easter":
            easter_eggs = bot.guild_config(interaction.guild.id).setdefault("easter_eggs", {})
            easter_eggs["enabled"] = not bool(easter_eggs.get("enabled", True))
            bot.save_config()
            try:
                view = build_settings_view(bot, interaction.guild)
                try:
                    bot.add_view(view)
                except Exception:
                    pass
                if interaction.response.is_done():
                    try:
                        await interaction.edit_original_response(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
                else:
                    try:
                        await interaction.response.edit_message(view=view)
                    except Exception:
                        try:
                            if interaction.message:
                                await interaction.followup.edit_message(interaction.message.id, view=view)
                            else:
                                await interaction.followup.send(view=view, ephemeral=True)
                        except Exception:
                            await interaction.followup.send(view=view, ephemeral=True)
            except Exception:
                try:
                    view = build_settings_view(bot, interaction.guild)
                    await interaction.followup.send(view=view, ephemeral=True)
                except Exception:
                    pass
            return
        elif action == "sched":
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Create scheduled messages with `/schedule create`.", ephemeral=True)
                else:
                    await interaction.response.send_message("Create scheduled messages with `/schedule create`.", ephemeral=True)
            except Exception:
                pass
            return
        else:
            return

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
        try:
            view = build_settings_view(self.bot, interaction.guild)
            try:
                self.bot.add_view(view)
            except Exception:
                pass
            await interaction.response.send_message(view=view, ephemeral=True)
            return
        except Exception:
            try:
                view = build_settings_view(self.bot, interaction.guild)
                await interaction.response.send_message(view=view, ephemeral=True)
                return
            except Exception:
                pass
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