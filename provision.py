"""Builds the server described in server_layout.toml.

    python provision.py            shows what would change, changes nothing
    python provision.py --apply    makes the changes and updates config.json

Stop the bot first: it keeps config.json in memory and would overwrite the result.
Nothing is ever deleted. Safe to run again after changing the layout.
"""

import argparse
import asyncio
import os
import tomllib
from pathlib import Path

import discord
from dotenv import load_dotenv

from bot import BASE_DIR, default_guild_config, validate_store
from config_store import ConfigStore
from emoji_utils import is_valid_emoji

MAX_MENU_ROLES = 25

MEMBER_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    send_messages_in_threads=True,
    create_public_threads=True,
    embed_links=True,
    attach_files=True,
    add_reactions=True,
    read_message_history=True,
    use_external_emojis=True,
    use_application_commands=True,
    change_nickname=True,
    connect=True,
    speak=True,
    stream=True,
    use_voice_activation=True,
)

# A role can only hand out permissions it holds itself, so the bot needs the member set too.
BOT_PERMISSIONS = MEMBER_PERMISSIONS | discord.Permissions(
    manage_roles=True,
    manage_channels=True,
    manage_guild=True,
    kick_members=True,
    ban_members=True,
    manage_messages=True,
    pin_messages=True,
    move_members=True,
)

READ_ONLY = {
    "send_messages": False,
    "send_messages_in_threads": False,
    "create_public_threads": False,
    "create_private_threads": False,
}
WRITER = {"view_channel": True, "send_messages": True, "send_messages_in_threads": True}

KINDS = {"text": discord.ChannelType.text, "voice": discord.ChannelType.voice}


def spo_role(studiengang: dict, spo: str) -> str:
    return f'{studiengang["key"]} {spo}'


def fach_emojis(layout: dict) -> dict[str, str]:
    """Role name (the label) -> emoji shown on its button."""
    return {
        fach["label"]: fach["emoji"]
        for group in layout.get("fach_kategorien", [])
        for fach in group["faecher"]
        if fach.get("emoji")
    }


def fach_labels(layout: dict) -> dict[str, str]:
    return {
        fach["channel"]: fach["label"]
        for group in layout.get("fach_kategorien", [])
        for fach in group["faecher"]
    }


def build_menus(layout: dict) -> list[dict]:
    channels = layout["channels"]
    texts = layout["texts"]
    roles = layout["roles"]

    def menu(menu_id: str, name: str, channel: str, entries: list[tuple[str, str]], **extra) -> dict:
        return {"id": menu_id, "name": name, "channel": channel, "entries": entries, "embed": {"title": name}, **extra}

    menus = [
        menu(
            "regeln",
            texts["rules_title"],
            channels["rules"],
            [(roles["member"], texts["rules_button"])],
            mode="accept",
            embed={
                "title": texts["rules_title"],
                "description": texts["rules"].strip().replace("{kontakt}", layout["kontakt"] or "?"),
            },
        ),
        menu(
            "studiengang",
            "Studiengang & SPO / Degree programme",
            channels["studiengangwahl"],
            [
                (spo_role(sg, spo), spo_role(sg, spo))
                for sg in layout["studiengaenge"]
                for spo in sg["spos"]
            ],
        ),
        menu(
            "sprache",
            "Language",
            channels["studiengangwahl"],
            [(roles["international"], "English please · International Student")],
        ),
        menu(
            "semester",
            "Semester",
            channels["semesterwahl"],
            [(name, name) for name in layout["semester"]["roles"]],
        ),
        menu(
            "events",
            "Events",
            channels["eventwahl"],
            [(event["role"], event["role"].removeprefix("Event: ")) for event in layout["events"]],
        ),
    ]
    labels = fach_labels(layout)
    for entry in layout.get("faecherwahl", []):
        menus.append(
            menu(
                f'fach-{entry["id"]}',
                entry["title"],
                entry["channel"],
                [(labels[name], labels[name]) for name in entry["faecher"]],
            )
        )
    return menus


def layout_errors(layout: dict) -> list[str]:
    errors = []
    labels = fach_labels(layout)
    all_fach = [
        fach["channel"]
        for group in layout.get("fach_kategorien", [])
        for fach in group["faecher"]
    ]
    for name in sorted({name for name in all_fach if all_fach.count(name) > 1}):
        errors.append(f'Fach "{name}" is listed twice in fach_kategorien.')
    if len(set(labels.values())) != len(labels):
        errors.append("Two subjects share a label, so they would share one role.")
    for label, emoji in fach_emojis(layout).items():
        if not is_valid_emoji(emoji):
            errors.append(f'"{label}" has an emoji Discord would not accept: {emoji!r}')
    semester = layout["semester"]
    if semester.get("colors") and len(semester["colors"]) != len(semester["roles"]):
        errors.append("[semester] needs exactly one colour per role.")
    known = {sg["channel"] for sg in layout["studiengaenge"]} | {layout["channels"].get("wahlfaecher")}
    seen_ids = set()
    for entry in layout.get("faecherwahl", []):
        if entry["id"] in seen_ids:
            errors.append(f'faecherwahl id "{entry["id"]}" is used twice.')
        seen_ids.add(entry["id"])
        if entry["channel"] not in known:
            errors.append(f'faecherwahl "{entry["id"]}": unknown channel "{entry["channel"]}".')
        for name in entry["faecher"]:
            if name not in labels:
                errors.append(f'faecherwahl "{entry["id"]}": unknown subject "{name}".')
    if errors:
        return errors
    for menu in build_menus(layout):
        if len(menu["entries"]) > MAX_MENU_ROLES:
            errors.append(
                f'Menu "{menu["id"]}" has {len(menu["entries"])} buttons, the limit is {MAX_MENU_ROLES}. Split it up.'
            )
    return errors


class Provisioner:
    def __init__(self, guild: discord.Guild, layout: dict, apply: bool) -> None:
        self.guild = guild
        self.layout = layout
        self.apply = apply
        self.changes: list[str] = []
        self.warnings: list[str] = []
        self.roles: dict[str, discord.Role | None] = {}
        self.channels: dict[str, discord.abc.GuildChannel | None] = {}

    def _role_key(self, key: str) -> discord.Role | None:
        return self.roles[self.layout["roles"][key]]

    async def ensure_role(
        self, name: str, permissions: discord.Permissions | None = None, color: str | None = None
    ) -> None:
        colour = discord.Colour(int(color.lstrip("#"), 16)) if color else discord.Colour.default()
        matches = [role for role in self.guild.roles if role.name == name]
        if len(matches) > 1:
            self.warnings.append(f'{len(matches)} roles are named "{name}", using the first one.')
        if matches:
            role = self.roles[name] = matches[0]
            if color and role.colour != colour:
                self.changes.append(f"set colour of {name}: {color}")
                if self.apply:
                    await role.edit(colour=colour, reason="provision.py")
            return
        self.changes.append(f"create role: {name}" + (f" ({color})" if color else ""))
        self.roles[name] = None
        if self.apply:
            self.roles[name] = await self.guild.create_role(
                name=name,
                permissions=permissions or discord.Permissions.none(),
                colour=colour,
                reason="provision.py",
            )

    async def ensure_category(self, name: str) -> discord.CategoryChannel | None:
        found = next((c for c in self.guild.categories if c.name == name), None)
        if found is not None:
            return found
        change = f"create category: {name}"
        if change not in self.changes:
            self.changes.append(change)
        return await self.guild.create_category(name, reason="provision.py") if self.apply else None

    async def ensure_channels(self, name: str, kind: str, category_name: str | None) -> list:
        """All channels with this name, created when missing. Names can repeat on Discord."""
        category_id = None
        if category_name is not None:
            category = await self.ensure_category(category_name)
            category_id = category.id if category else -1
        matches = [
            channel
            for channel in self.guild.channels
            if channel.name == name
            and channel.type == KINDS[kind]
            and (category_name is None or channel.category_id == category_id)
        ]
        if len(matches) > 1:
            self.warnings.append(
                f'{len(matches)} channels are named "{name}" in {category_name or "the server"}, '
                "each gets the same permissions. Delete the extra one if it is a duplicate."
            )
        if matches:
            return matches
        self.changes.append(f"create {kind} channel: {category_name or '(no category)'} / {name}")
        if not self.apply:
            return []
        parent = next((c for c in self.guild.categories if c.name == category_name), None)
        create = self.guild.create_voice_channel if kind == "voice" else self.guild.create_text_channel
        return [await create(name, category=parent, reason="provision.py")]

    async def set_perms(self, channels: list, target, label: str, **perms) -> None:
        """Only touches the named permissions and keeps whatever else is set."""
        for channel in channels:
            if target is None:
                self.changes.append(f"set {label} on {channel.name}: {perms}")
                continue
            current = channel.overwrites_for(target)
            if all(getattr(current, key) == value for key, value in perms.items()):
                continue
            self.changes.append(f"set {label} on {channel.name}: {perms}")
            if self.apply:
                current.update(**perms)
                try:
                    await channel.set_permissions(target, overwrite=current, reason="provision.py")
                except discord.Forbidden:
                    self.warnings.append(f"FAILED (missing permission): set {label} on {channel.name}: {perms}")

    def _warn_foreign_access(self, channel, allowed: set) -> None:
        for target, overwrite in channel.overwrites.items():
            if target in allowed or target == self.guild.me or overwrite.view_channel is not True:
                continue
            self.warnings.append(f'{channel.name}: "{target}" can see this private channel but is not in the layout.')

    async def _keep_bot_access(self, channels: list) -> None:
        """Must run before anything is restricted: once the bot can't see a channel or lacks a
        permission there, Discord no longer lets it change that channel's permissions."""
        await self.set_perms(channels, self.guild.me, "bot", view_channel=True)

    async def _private(self, channels: list, allowed_keys: list[str]) -> None:
        """Hidden from @everyone. Staff roles not named are explicitly locked out."""
        everyone = self.guild.default_role
        await self._keep_bot_access(channels)
        await self.set_perms(channels, everyone, "@everyone", view_channel=False)
        allowed = set()
        for key in ("admin", "fachschaft", "fsv"):
            role = self._role_key(key)
            is_allowed = key in allowed_keys
            if is_allowed and role is not None:
                allowed.add(role)
            await self.set_perms(channels, role, self.layout["roles"][key], view_channel=is_allowed)
        for channel in channels:
            self._warn_foreign_access(channel, allowed)

    async def _hidden_for(self, channels: list, roles: list[tuple[discord.Role | None, str]]) -> None:
        await self._keep_bot_access(channels)
        await self.set_perms(channels, self.guild.default_role, "@everyone", view_channel=False)
        for role, label in roles:
            await self.set_perms(channels, role, label, view_channel=True)

    async def _menu_channel(self, channels: list) -> None:
        """Read-only for people; the bot posts its menus here."""
        await self._keep_bot_access(channels)
        await self.set_perms(
            channels, self.guild.me, "bot", embed_links=True, read_message_history=True, **WRITER
        )
        await self.set_perms(channels, self.guild.default_role, "@everyone", **READ_ONLY)
        await self.set_perms(channels, self._role_key("admin"), self.layout["roles"]["admin"], **WRITER)

    async def build_roles(self) -> None:
        layout = self.layout
        names = layout["roles"]
        await self.ensure_role(names["member"], MEMBER_PERMISSIONS)
        for key in ("international", "admin", "fachschaft", "fsv"):
            await self.ensure_role(names[key])
        for sg in layout["studiengaenge"]:
            for spo in sg["spos"]:
                await self.ensure_role(spo_role(sg, spo), color=sg.get("color"))
        semester = layout["semester"]
        for name, color in zip(semester["roles"], semester.get("colors") or [None] * len(semester["roles"])):
            await self.ensure_role(name, color=color)
        for event in layout["events"]:
            await self.ensure_role(event["role"], color=layout.get("events_color"))
        for label in fach_labels(layout).values():
            await self.ensure_role(label)

    async def build_entry_channels(self) -> None:
        cfg = self.layout["channels"]
        everyone = self.guild.default_role

        honeypot = await self.ensure_channels(cfg["honeypot"], "text", None)
        self.channels[cfg["honeypot"]] = honeypot[0] if honeypot else None
        await self.set_perms(
            honeypot, everyone, "@everyone", view_channel=True, send_messages=True, read_message_history=True
        )

        rules = await self.ensure_channels(cfg["rules"], "text", cfg["rules_category"])
        self.channels[cfg["rules"]] = rules[0] if rules else None
        await self._menu_channel(rules)
        await self.set_perms(rules, everyone, "@everyone", view_channel=True, read_message_history=True)

    async def build_welcome_channel(self) -> None:
        cfg = self.layout["channels"]
        channels = await self.ensure_channels(cfg["welcome"], "text", cfg["rules_category"])
        await self._menu_channel(channels)
        if not channels or self.guild.system_channel != channels[0]:
            self.changes.append(f"send join messages to: {cfg['welcome']}")
            if self.apply:
                await self._edit_guild(
                    f"Server Settings > Overview > System Messages Channel: {cfg['welcome']}",
                    system_channel=channels[0],
                )

    async def _edit_guild(self, manual_step: str, **fields) -> None:
        try:
            await self.guild.edit(reason="provision.py", **fields)
        except discord.HTTPException as error:
            self.warnings.append(f"MANUAL: Discord refused this ({error.text}), please set it by hand: {manual_step}")

    async def build_menu_channels(self) -> None:
        cfg = self.layout["channels"]
        category = cfg["menu_category"]
        for name in filter(None, (cfg["studiengangwahl"], cfg["semesterwahl"], cfg.get("wahlfaecher"), cfg["eventwahl"])):
            channels = await self.ensure_channels(name, "text", category)
            self.channels[name] = channels[0] if channels else None
            await self._menu_channel(channels)

        for sg in self.layout["studiengaenge"]:
            channels = await self.ensure_channels(sg["channel"], "text", category)
            self.channels[sg["channel"]] = channels[0] if channels else None
            await self._menu_channel(channels)
            names = [spo_role(sg, spo) for spo in sg["spos"]]
            await self._hidden_for(
                channels,
                [(self.roles[n], n) for n in names]
                + [(self._role_key("admin"), self.layout["roles"]["admin"]), (self.guild.me, "bot")],
            )

    async def build_fach_channels(self) -> None:
        admin = (self._role_key("admin"), self.layout["roles"]["admin"])
        for group in self.layout["fach_kategorien"]:
            category = await self.ensure_category(group["category"])
            if category is not None:
                await self._hidden_for([category], [admin])
            for fach in group["faecher"]:
                channels = await self.ensure_channels(fach["channel"], "text", group["category"])
                await self._hidden_for(channels, [(self.roles[fach["label"]], fach["label"]), admin])

    async def build_event_channels(self) -> None:
        admin = self.layout["roles"]["admin"]
        for event in self.layout["events"]:
            channels = await self.ensure_channels(event["channel"], "text", "Events")
            await self.set_perms(channels, self.guild.default_role, "@everyone", **READ_ONLY)
            await self.set_perms(channels, self._role_key("admin"), admin, **WRITER)
            await self.set_perms(channels, self.roles[event["role"]], event["role"], **WRITER)

    async def build_private_channels(self) -> None:
        for group in self.layout["private_categories"]:
            category = await self.ensure_category(group["category"])
            if category is not None:
                await self._private([category], group["roles"])
            for spec in group["channels"]:
                channels = await self.ensure_channels(spec["name"], spec["kind"], group["category"])
                await self._private(channels, spec["roles"])
                if spec.get("bot"):
                    await self.set_perms(channels, self.guild.me, "bot", embed_links=True, **WRITER)
                if spec.get("log"):
                    self.channels["log"] = channels[0] if channels else None

    async def build_voice(self) -> None:
        cfg = self.layout["channels"]
        hub = await self.ensure_channels(cfg["hub"], "voice", cfg["voice_category"])
        self.channels["hub"] = hub[0] if hub else None
        afk = await self.ensure_channels(cfg["afk"], "voice", cfg["voice_category"])
        await self.set_perms(afk, self.guild.default_role, "@everyone", speak=False)
        timeout = self.layout["afk_timeout_seconds"]
        if afk and (self.guild.afk_channel != afk[0] or self.guild.afk_timeout != timeout):
            self.changes.append(f"set AFK channel: {cfg['afk']} after {timeout}s")
            if self.apply:
                await self._edit_guild(
                    f"Server Settings > Overview > Inactive Channel: {cfg['afk']}, {timeout // 60} minutes",
                    afk_channel=afk[0],
                    afk_timeout=timeout,
                )
        elif not afk:
            self.changes.append(f"set AFK channel: {cfg['afk']} after {timeout}s")

    async def pin_honeypot_warning(self, config: dict) -> None:
        cfg = self.layout["channels"]
        channel = self.channels[cfg["honeypot"]]
        texts = self.layout["texts"]
        if channel is None:
            return
        if channel.topic != texts["honeypot_topic"]:
            self.changes.append(f"set topic of {channel.name}")
            if self.apply:
                await channel.edit(topic=texts["honeypot_topic"], reason="provision.py")
        if config.get("provision_state", {}).get("honeypot_warning_message_id"):
            return
        self.changes.append(f"post and pin the warning in {channel.name}")
        if self.apply:
            text = texts["honeypot_warning"].strip().replace("{kontakt}", self.layout["kontakt"])
            message = await channel.send(text)
            await message.pin(reason="provision.py")
            config.setdefault("provision_state", {})["honeypot_warning_message_id"] = str(message.id)

    def update_config(self, config: dict) -> None:
        """Only called with --apply, so all roles and channels exist."""
        roles = self.layout["roles"]
        cfg = self.layout["channels"]
        config["kick_channel_id"] = str(self.channels[cfg["honeypot"]].id)
        config["kick_action"] = "ban"
        config["log_channel_id"] = str(self.channels["log"].id)
        config["timezone"] = self.layout["timezone"]
        config["international_role_id"] = str(self.roles[roles["international"]].id)
        config.setdefault("easter_eggs", {})["enabled"] = False
        admin_id = str(self.roles[roles["admin"]].id)
        if admin_id not in config.setdefault("admin_roles", []):
            config["admin_roles"].append(admin_id)
        config.setdefault("temp_voice", {"channels": {}})["hub_channel_id"] = str(self.channels["hub"].id)

        existing = {menu["id"]: menu for menu in config.setdefault("role_menus", [])}
        emojis = fach_emojis(self.layout)
        for spec in build_menus(self.layout):
            if not spec["entries"]:
                self.warnings.append(f'Menu "{spec["id"]}" has no buttons and was skipped.')
                continue
            channel_id = str(self.channels[spec["channel"]].id)
            menu = existing.get(spec["id"])
            if menu is None:
                menu = {"id": spec["id"], "message_id": None}
                config["role_menus"].append(menu)
            elif menu.get("channel_id") != channel_id:
                menu["message_id"] = None
            menu.update(
                guild_id=str(self.guild.id),
                name=spec["name"],
                channel_id=channel_id,
                embed=spec["embed"],
                mode=spec.get("mode", "toggle"),
                roles=[
                    {"role_id": str(self.roles[role_name].id), "label": label}
                    | ({"emoji": emojis[role_name]} if role_name in emojis else {})
                    for role_name, label in spec["entries"]
                ],
            )

        # Keep layout order so reposted menus appear in that order. Subject menus that left the layout are
        # dropped, menus added by hand in /settings stay at the end.
        order = {spec["id"]: index for index, spec in enumerate(build_menus(self.layout))}
        for menu in config["role_menus"]:
            if menu["id"] not in order and menu["id"].startswith("fach-"):
                self.changes.append(f'remove menu from config: {menu["id"]} (delete its message by hand)')
        config["role_menus"] = sorted(
            (m for m in config["role_menus"] if m["id"] in order or not m["id"].startswith("fach-")),
            key=lambda m: order.get(m["id"], len(order)),
        )

    def check_manual_steps(self) -> None:
        guild = self.guild
        names = self.layout["roles"]
        me = guild.me
        if guild.default_role.permissions.view_channel:
            self.warnings.append("MANUAL: @everyone still has 'View Channels' server-wide. Turn it off last.")
        admin = next((r for r in guild.roles if r.name == names["admin"]), None)
        if admin is not None and admin.permissions.administrator:
            self.warnings.append(
                f'MANUAL: "{admin.name}" has Administrator and can see Fachschaft channels. Replace it (see README).'
            )
        ranked = [admin]
        for key in ("fsv", "fachschaft"):
            role = next((r for r in guild.roles if r.name == names[key]), None)
            ranked.append(role)
            if role is None:
                continue
            perms = role.permissions
            if key == "fsv" and not (perms.moderate_members and perms.mute_members):
                self.warnings.append(f'MANUAL: give "{role.name}" the Timeout and Mute Members permissions.')
            if perms.kick_members or perms.ban_members:
                self.warnings.append(f'MANUAL: remove Kick and Ban from "{role.name}", those stay with admins.')
        ranked.append(me.top_role)
        ranked = [role for role in ranked if role is not None]
        if any(upper < lower for upper, lower in zip(ranked, ranked[1:])):
            order = " > ".join(role.name for role in ranked)
            self.warnings.append(f"MANUAL: sort the roles like this: {order}, then all others.")

    async def run(self, config: dict) -> None:
        await self.build_roles()
        await self.build_entry_channels()
        await self.build_welcome_channel()
        await self.build_menu_channels()
        await self.build_fach_channels()
        await self.build_event_channels()
        await self.build_private_channels()
        await self.build_voice()
        await self.pin_honeypot_warning(config)
        if self.apply:
            self.update_config(config)
        self.check_manual_steps()


def pick_guild(guilds: list[discord.Guild], guild_id: int) -> discord.Guild:
    if guild_id:
        guild = next((g for g in guilds if g.id == guild_id), None)
        if guild is None:
            raise SystemExit(f"The bot is not on the server {guild_id}.")
        return guild
    if len(guilds) != 1:
        names = ", ".join(f"{g.name} ({g.id})" for g in guilds) or "none"
        raise SystemExit(f"Set guild_id in server_layout.toml. The bot is on these servers: {names}")
    return guilds[0]


async def provision(layout: dict, apply: bool) -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set.")

    store = ConfigStore(BASE_DIR / "config.json")
    validate_store(store.data)

    client = discord.Client(intents=discord.Intents.default())
    async with client:
        await client.login(token)
        connection = asyncio.create_task(client.connect())
        await client.wait_until_ready()
        guild = pick_guild(client.guilds, layout["guild_id"])
        print(f"Server: {guild.name} ({guild.id})")
        config = store.data.setdefault("servers", {}).setdefault(str(guild.id), default_guild_config())

        missing = [name for name, needed in BOT_PERMISSIONS if needed and not getattr(guild.me.guild_permissions, name)]
        if missing:
            print("Bot is missing permissions:", ", ".join(missing))
            if apply:
                raise SystemExit("Nothing was changed. Fix the bot role and try again.")

        # Discord only lets the bot set channel permissions for roles below its own.
        above = [
            role.name
            for role in guild.roles
            if role.name in {layout["roles"][key] for key in ("admin", "fachschaft", "fsv")}
            and role >= guild.me.top_role
        ]
        if above:
            print(
                f"Note: the bot role is below {', '.join(above)}, so it cannot change their channel permissions. "
                "That is fine once the server is set up. For a first setup, drag it to the top and give it "
                "Administrator while this runs, then sort the roles back as described in the README."
            )

        provisioner = Provisioner(guild, layout, apply)
        try:
            await provisioner.run(config)
        finally:
            if apply:
                store.save()
            connection.cancel()

    print(f'{"Done" if apply else "Would change"}: {len(provisioner.changes)} item(s)')
    for change in provisioner.changes:
        print(" ", change)
    for warning in provisioner.warnings:
        print("WARNING:", warning)
    if apply:
        print('Next: start the bot and press /settings > Role menus > "Repost / refresh all".')
    else:
        print("Nothing was changed. Run with --apply to make these changes.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Discord server from server_layout.toml")
    parser.add_argument("--apply", action="store_true", help="make the changes instead of only showing them")
    parser.add_argument("--layout", type=Path, default=BASE_DIR / "server_layout.toml")
    args = parser.parse_args()

    load_dotenv()
    with open(args.layout, "rb") as f:
        layout = tomllib.load(f)

    errors = layout_errors(layout)
    if args.apply and not layout["kontakt"]:
        errors.append("kontakt is not set, but the rules and the honeypot warning need it.")
    if errors:
        raise SystemExit("server_layout.toml has problems:\n- " + "\n- ".join(errors))

    asyncio.run(provision(layout, args.apply))


if __name__ == "__main__":
    main()
