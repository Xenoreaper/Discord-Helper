import discord
from discord.ext import commands

from emoji_utils import is_valid_emoji


def build_embed(bot: commands.Bot, menu_spec: dict) -> discord.Embed:
    embed_spec = menu_spec.get("embed") or {}
    embed = discord.Embed(
        title=embed_spec.get("title"),
        description=embed_spec.get("description"),
        color=discord.Color(int(embed_spec.get("color", 0x5865F2))),
    )
    if embed_spec.get("footer"):
        embed.set_footer(text=embed_spec["footer"])
    return embed


def _build_buttons(bot: commands.Bot, menu_spec: dict) -> list["RoleButton"]:
    guild = bot.get_guild(int(bot.config["guild_id"]))
    buttons: list[RoleButton] = []
    for role_spec in menu_spec.get("roles", []):
        role = guild.get_role(int(role_spec["role_id"])) if guild else None
        if role is None or role.is_default() or role.managed:
            continue
        label = (role_spec.get("label") or role.name)[:80]
        emoji = role_spec.get("emoji") if is_valid_emoji(role_spec.get("emoji")) else None
        buttons.append(RoleButton(menu_spec["id"], role.id, label, emoji))
    return buttons


class RoleButton(discord.ui.Button):
    def __init__(self, menu_id: str, role_id: int, label: str, emoji: str | None = None) -> None:
        super().__init__(
            label=label or "Role",
            emoji=emoji,
            style=discord.ButtonStyle.primary,
            custom_id=f"role_menu:{menu_id}:{role_id}",
        )
        self.menu_id = menu_id
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("This menu only works inside a server.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("That role no longer exists.", ephemeral=True)
            return
        if role.is_default() or role.managed:
            await interaction.response.send_message("That role can't be given out.", ephemeral=True)
            return

        bot_member = guild.me
        if bot_member is None or role >= bot_member.top_role:
            await interaction.response.send_message(
                "I can't manage that role because it is above my highest role.", ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Role menu")
                text = f"Removed the **{role.name}** role."
            else:
                await member.add_roles(role, reason="Role menu")
                text = f"You now have the **{role.name}** role."
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change that role.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Something went wrong, please try again.", ephemeral=True)
            return

        await interaction.response.send_message(text, ephemeral=True)


class RoleMenuView(discord.ui.View):
    def __init__(self, bot: commands.Bot, menu_spec: dict) -> None:
        super().__init__(timeout=None)
        self.menu_id = menu_spec["id"]
        for button in _build_buttons(bot, menu_spec):
            self.add_item(button)


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.views: dict[str, RoleMenuView] = {}
        self.rebuild_views()

    def rebuild_views(self) -> None:
        self.views = {}
        for menu in self.bot.config.get("role_menus", []):
            view = RoleMenuView(self.bot, menu)
            self.views[menu["id"]] = view
            message_id = menu.get("message_id")
            self.bot.add_view(view, message_id=int(message_id) if message_id else None)

    def _menu_channel(self, menu: dict) -> tuple[discord.TextChannel | None, str]:
        guild = self.bot.get_guild(int(self.bot.config["guild_id"]))
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
        view = self.views.get(menu["id"]) or RoleMenuView(self.bot, menu)
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
        guild = self.bot.get_guild(int(self.bot.config["guild_id"]))
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

    async def refresh_all(self) -> tuple[int, int]:
        posted = failed = 0
        for menu in self.bot.config.get("role_menus", []):
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
        self.bot.config["role_menus"].remove(menu)
        self.bot.save_config()
        self.rebuild_views()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))