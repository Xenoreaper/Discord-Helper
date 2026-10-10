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


def validate_config(config: dict) -> None:
    if not _is_id(config.get("guild_id")):
        raise SystemExit('config.json "guild_id" must be a numeric Discord ID.')

    for key in ("kick_channel_id", "log_channel_id"):
        value = config.get(key)
        if value not in (None, "") and not _is_id(value):
            raise SystemExit(f'config.json "{key}" must be a numeric channel ID or null.')

    join = config.setdefault("join", {})
    if not _is_id(join.get("base_role_id")) and join.get("base_role_id") not in (None, ""):
        raise SystemExit('config.json "join.base_role_id" must be a numeric role ID or null.')

    invites = join.setdefault("invites", {})
    for code, role_id in invites.items():
        if str(code).isdigit() and not _is_id(role_id):
            raise SystemExit(
                'config.json "join.invites" looks swapped: the key '
                f'"{code}" is a role ID but the value is not. The format is '
                '{"inviteCode": "roleId"} (key = invite code, value = role ID).'
            )
        if not _is_id(role_id):
            raise SystemExit(
                f'config.json join.invites["{code}"] must be a numeric role ID. '
                'The key must be the invite code (like "GC53JhrDF").'
            )

    _migrate_role_menu(config)

    menus = config.setdefault("role_menus", [])
    if not isinstance(menus, list):
        raise SystemExit('config.json "role_menus" must be a list.')
    seen: set[str] = set()
    for i, menu in enumerate(menus):
        if not isinstance(menu, dict):
            raise SystemExit(f'config.json "role_menus[{i}]" must be an object.')
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


class RoleBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.store = ConfigStore(BASE_DIR / "config.json")
        validate_config(self.store.data)
        self.config: dict = self.store.data

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
        for extension in ("cogs.reaction_roles", "cogs.join_roles", "cogs.guard", "cogs.settings"):
            await self.load_extension(extension)

        guild = discord.Object(id=int(self.config["guild_id"]))
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Synced app commands to guild %s", guild.id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        cog = self.get_cog("ReactionRoles")
        if cog is not None:
            cog.rebuild_views()


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
