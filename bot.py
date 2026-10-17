import asyncio
import logging
import os
import re
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config_store import ConfigStore

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("rolebot")


def _is_id(value) -> bool:
    return value not in (None, "") and str(value).isdigit()


def default_guild_config() -> dict:
    return {
        "kick_channel_id": None,
        "kick_action": "kick",
        "log_channel_id": None,
        "kick_exempt_roles": [],
        "admin_roles": [],
        "join": {"base_role_id": None, "invites": {}},
        "role_menus": [],
        "grant_permissions": {"users": {}, "roles": {}},
        "easter_eggs": {},
    }


def _migrate_role_menu(config: dict) -> None:
    if "role_menu" not in config:
        return
    old = config.get("role_menu") or {}
    if not isinstance(old, dict):
        old = {}
    old_roles = old.get("roles")
    if old_roles is None and old.get("role_ids") is not None:
        old_roles = [{"role_id": role_id} for role_id in old["role_ids"]]
    config["role_menus"] = [
        {
            "id": "menu",
            "name": "Roles",
            "channel_id": config.get("rolemenu_channel_id") or None,
            "message_id": None,
            "embed": old.get("embed") or {},
            "roles": old_roles or [],
        }
    ]
    config.pop("role_menu", None)
    config.pop("rolemenu_channel_id", None)


def _validate_roles(roles, path: str) -> None:
    if not isinstance(roles, list):
        raise SystemExit(f'config.json "{path}" must be a list.')
    for i, item in enumerate(roles):
        if not isinstance(item, dict) or not _is_id(item.get("role_id")):
            raise SystemExit(f'config.json "{path}[{i}].role_id" must be a numeric role ID.')
        limit = item.get("limit")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise SystemExit(f'config.json "{path}[{i}].limit" must be a positive integer or null.')


def validate_config(config: dict) -> None:
    for key in ("kick_channel_id", "log_channel_id"):
        value = config.get(key)
        if value not in (None, "") and not _is_id(value):
            raise SystemExit(f'config.json "{key}" must be a numeric channel ID or null.')

    action = config.get("kick_action")
    if action not in (None, "kick", "ban"):
        raise SystemExit('config.json "kick_action" must be "kick" or "ban".')

    exempt_roles = config.setdefault("kick_exempt_roles", [])
    if not isinstance(exempt_roles, list):
        raise SystemExit('config.json "kick_exempt_roles" must be a list of role IDs.')
    for i, role_id in enumerate(exempt_roles):
        if not _is_id(role_id):
            raise SystemExit(f'config.json "kick_exempt_roles[{i}]" must be a numeric role ID or null.')

    admin_roles = config.setdefault("admin_roles", [])
    if not isinstance(admin_roles, list):
        raise SystemExit('config.json "admin_roles" must be a list of role IDs.')
    for i, role_id in enumerate(admin_roles):
        if not _is_id(role_id):
            raise SystemExit(f'config.json "admin_roles[{i}]" must be a numeric role ID or null.')

    _validate_grant_permissions(config)

    join = config.setdefault("join", {})
    if not _is_id(join.get("base_role_id")) and join.get("base_role_id") not in (None, ""):
        raise SystemExit('config.json "join.base_role_id" must be a numeric role ID or null.')

    invites = join.setdefault("invites", {})
    for code, spec in invites.items():
        role_id = spec if isinstance(spec, str) else (spec.get("role_id") if isinstance(spec, dict) else None)
        if str(code).isdigit() and not _is_id(role_id):
            raise SystemExit(
                'config.json "join.invites" looks swapped: the key '
                f'"{code}" is a role ID but the value is not. The format is '
                '{"inviteCode": "roleId"} (key = invite code, value = role ID).'
            )
        if not _is_id(role_id):
            raise SystemExit(
                f'config.json join.invites["{code}"] must map to a numeric role ID. '
                'The key must be the invite code (like "GC53JhrDF").'
            )
        if isinstance(spec, dict):
            for field in ("max_uses", "within_days", "used"):
                value = spec.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise SystemExit(
                        f'config.json join.invites["{code}"].{field} must be a non-negative integer or null.'
                    )

    _migrate_role_menu(config)

    _validate_easter_eggs(config)

    menus = config.setdefault("role_menus", [])
    if not isinstance(menus, list):
        raise SystemExit('config.json "role_menus" must be a list.')
    seen: set[str] = set()
    for i, menu in enumerate(menus):
        if not isinstance(menu, dict):
            raise SystemExit(f'config.json "role_menus[{i}]" must be an object.')
        menu.setdefault("guild_id", None)
        menu_id = menu.get("id")
        if not isinstance(menu_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", menu_id):
            raise SystemExit(
                f'config.json "role_menus[{i}].id" must be 1-40 characters '
                'of letters, digits, "-" or "_".'
            )
        if menu_id in seen:
            raise SystemExit(f'config.json has duplicate role menu id "{menu_id}".')
        seen.add(menu_id)
        menu.setdefault("name", menu_id)
        menu.setdefault("message_id", None)
        menu.setdefault("embed", {})
        if not isinstance(menu.get("embed"), dict):
            raise SystemExit(f'config.json "role_menus[{i}].embed" must be an object.')
        for key in ("channel_id", "message_id"):
            value = menu.get(key)
            if value not in (None, "") and not _is_id(value):
                raise SystemExit(f'config.json "role_menus[{i}].{key}" must be a numeric ID or null.')

        roles = menu.get("roles")
        if roles is None and menu.get("role_ids") is not None:
            menu["roles"] = [{"role_id": role_id} for role_id in menu["role_ids"]]
            menu.pop("role_ids", None)
            roles = menu["roles"]
        menu.setdefault("roles", [])
        _validate_roles(roles, f"role_menus[{i}].roles")


def _validate_grant_permissions(config: dict) -> None:
    grant = config.setdefault("grant_permissions", {})
    if not isinstance(grant, dict):
        raise SystemExit('config.json "grant_permissions" must be an object.')
    for bucket in ("users", "roles"):
        entries = grant.setdefault(bucket, {})
        if not isinstance(entries, dict):
            raise SystemExit(f'config.json "grant_permissions.{bucket}" must be an object.')
        for subject_id, role_ids in entries.items():
            if not _is_id(subject_id):
                raise SystemExit(
                    f'config.json "grant_permissions.{bucket}" has a non-numeric ID "{subject_id}".'
                )
            if not isinstance(role_ids, list):
                raise SystemExit(
                    f'config.json "grant_permissions.{bucket}[\'{subject_id}\']" must be a list of role IDs.'
                )
            for i, role_id in enumerate(role_ids):
                if not _is_id(role_id):
                    raise SystemExit(
                        f'config.json "grant_permissions.{bucket}[\'{subject_id}\'][{i}]" '
                        "must be a numeric role ID."
                    )


def _validate_easter_eggs(config: dict) -> None:
    easter_eggs = config.setdefault("easter_eggs", {})
    if not isinstance(easter_eggs, dict):
        raise SystemExit('config.json "easter_eggs" must be an object.')
    for key, value in easter_eggs.items():
        if key == "enabled":
            if value is not None and not isinstance(value, bool):
                raise SystemExit('config.json "easter_eggs.enabled" must be a boolean or null.')
        elif value is not None and not isinstance(value, str):
            raise SystemExit(f'config.json "easter_eggs.{key}" must be a string or null.')


def _migrate_legacy(store: dict) -> None:
    if "guild_id" not in store:
        return
    gid = str(store.pop("guild_id"))
    config = {
        key: store.pop(key)
        for key in ("kick_channel_id", "kick_action", "log_channel_id", "kick_exempt_roles", "join", "role_menus")
        if key in store
    }
    validate_config(config)
    store.setdefault("servers", {})[gid] = config


def validate_store(store: dict) -> None:
    _migrate_legacy(store)
    servers = store.setdefault("servers", {})
    if not isinstance(servers, dict):
        raise SystemExit('config.json "servers" must be an object mapping guild IDs to configs.')
    for gid, config in servers.items():
        if not _is_id(gid):
            raise SystemExit(f'config.json "servers" has a non-numeric guild ID "{gid}".')
        if not isinstance(config, dict):
            raise SystemExit(f'config.json "servers[{gid}]" must be an object.')
        validate_config(config)


class RoleBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.store = ConfigStore(BASE_DIR / "config.json")
        validate_store(self.store.data)
        self.configs: dict = self.store.data.setdefault("servers", {})

    def guild_config(self, guild_id) -> dict:
        key = str(guild_id)
        if key not in self.configs:
            self.configs[key] = default_guild_config()
            self.save_config()
        return self.configs[key]

    def is_bot_admin(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        admin_roles = self.guild_config(member.guild.id).get("admin_roles", [])
        return any(member.get_role(int(role_id)) is not None for role_id in admin_roles or [])

    def save_config(self) -> bool:
        try:
            self.store.save()
        except OSError:
            log.exception(
                "Could not write config.json — make sure the file is writable by the "
                "bot's user (uid 1000 in Docker)."
            )
            return False
        return True

    async def setup_hook(self) -> None:
        for extension in (
            "cogs.reaction_roles",
            "cogs.join_roles",
            "cogs.guard",
            "cogs.settings",
            "cogs.grant",
            "cogs.easter_eggs",
        ):
            await self.load_extension(extension)

        await self.tree.sync()
        log.info("Synced app commands globally (available in every server)")

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        status = os.getenv("BOT_STATUS")
        if status:
            await self.change_presence(activity=discord.CustomActivity(name=status))
        for guild in self.guilds:
            self.guild_config(guild.id)
        if len(self.guilds) > 1:
            log.info("Connected to %d servers: %s", len(self.guilds), ", ".join(g.name for g in self.guilds))
        cog = self.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.guild_config(guild.id)
        await self.tree.sync()
        log.info("Joined %s (%s)", guild.name, guild.id)


async def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and add your bot token.")

    async with RoleBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
