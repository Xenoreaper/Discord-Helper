import logging
import re
import secrets

import discord

from emoji_utils import is_valid_emoji

log = logging.getLogger("rolebot.web")

DEFAULT_COLOR = 0x5865F2
_MENU_FIELD = re.compile(r"^menu_(\d+)_(name|channel|title|description|color|footer)$")
_MENU_META = re.compile(r"^menu_(\d+)_(id|message_id)$")
_MENU_ROLE = re.compile(r"^menu_(\d+)_role_(\d+)_(id|label|emoji|limit)$")


def _id_or_none(value) -> str | None:
    value = (value or "").strip()
    return str(int(value)) if value.isdigit() else None


def _id_list(values) -> list[str]:
    return [str(int(value)) for value in values if (value or "").strip().isdigit()]


def _parse_color(value) -> int:
    value = (value or "").strip().lstrip("#")
    if not value:
        return DEFAULT_COLOR
    if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return int(value, 16)
    if value.isdigit() and int(value) <= 0xFFFFFF:
        return int(value)
    raise ValueError(value)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text.strip().lower()).strip("-")[:40]


def _invite_rows(config: dict) -> list[dict]:
    rows = []
    for code, spec in config.get("join", {}).get("invites", {}).items():
        role_id = spec if isinstance(spec, str) else spec.get("role_id")
        row = {"code": code, "role_id": role_id or "", "max_uses": "", "within_days": "", "used": ""}
        if isinstance(spec, dict):
            row["max_uses"] = spec.get("max_uses") or ""
            row["within_days"] = spec.get("within_days") or ""
            row["used"] = spec.get("used") or ""
        rows.append(row)
    return rows


async def fetch_invite_options(guild: discord.Guild) -> list[dict]:
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.warning("Could not fetch invites for %s", guild.name, exc_info=True)
        return []
    options = []
    for invite in invites:
        label = f"{invite.code} • {invite.uses} uses"
        channel_name = getattr(invite.channel, "name", None)
        if channel_name:
            label += f" • #{channel_name}"
        options.append({"code": invite.code, "label": label})
    return options


def build_context(bot, guild: discord.Guild) -> dict:
    config = bot.guild_config(guild.id)
    channels = [c for c in guild.channels if isinstance(c, discord.TextChannel)]
    channels.sort(key=lambda c: (c.category.position if c.category else -1, c.position))
    roles = [r for r in guild.roles if not r.is_default() and not r.managed]
    roles.sort(key=lambda r: r.position, reverse=True)
    return {
        "guild": guild,
        "config": config,
        "channels": channels,
        "roles": roles,
        "channel_map": {str(c.id): c.name for c in channels},
        "role_map": {str(r.id): r.name for r in roles},
        "invites": _invite_rows(config),
        "menus": config.get("role_menus", []),
        "easter": config.get("easter_eggs") or {},
    }


def _parse_invites(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    invites: dict[str, dict] = {}
    codes = form.getall("invite_code", [])
    roles = form.getall("invite_role", [])
    maxes = form.getall("invite_max", [])
    days = form.getall("invite_days", [])
    used = form.getall("invite_used", [])
    for index, raw_code in enumerate(codes):
        code = (raw_code or "").strip()
        if not code:
            continue
        role = (roles[index] if index < len(roles) else "").strip()
        if not role.isdigit():
            errors.append(f"Invite {code}: choose a role.")
            continue
        spec: dict = {"role_id": role}
        for field, values in (("max_uses", maxes), ("within_days", days), ("used", used)):
            raw = (values[index] if index < len(values) else "").strip()
            if raw:
                if not raw.isdigit():
                    errors.append(f"Invite {code}: {field} must be a whole number.")
                    continue
                spec[field] = int(raw)
        invites[code] = spec
    return invites, errors


def _parse_menus(form, guild: discord.Guild) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    menus: dict[int, dict] = {}
    roles: dict[tuple[int, int], dict] = {}

    for key in set(form.keys()):
        if (match := _MENU_FIELD.match(key)) or (match := _MENU_META.match(key)):
            menus.setdefault(int(match.group(1)), {})[match.group(2)] = form.get(key, "")
        elif match := _MENU_ROLE.match(key):
            key_index = (int(match.group(1)), int(match.group(2)))
            roles.setdefault(key_index, {})[match.group(3)] = form.get(key, "")

    result: list[dict] = []
    seen_ids: set[str] = set()
    for index in sorted(menus):
        data = menus[index]
        role_specs: list[dict] = []
        for menu_index, role_index in sorted(k for k in roles if k[0] == index):
            role_data = roles[(menu_index, role_index)]
            role_id = (role_data.get("id") or "").strip()
            if not role_id.isdigit():
                if any((v or "").strip() for v in role_data.values()):
                    errors.append(f"Menu {index + 1}: a role row is missing its role.")
                continue
            spec: dict = {"role_id": role_id}
            if label := (role_data.get("label") or "").strip():
                spec["label"] = label[:80]
            if emoji := (role_data.get("emoji") or "").strip():
                if is_valid_emoji(emoji):
                    spec["emoji"] = emoji
                else:
                    errors.append(f"Menu {index + 1}: `{emoji}` is not a valid emoji.")
            if limit := (role_data.get("limit") or "").strip():
                if limit.isdigit() and int(limit) > 0:
                    spec["limit"] = int(limit)
                else:
                    errors.append(f"Menu {index + 1}: role limit must be a positive whole number.")
            role_specs.append(spec)

        name = (data.get("name") or "").strip()
        if not name and not role_specs:
            continue
        menu_id = (data.get("id") or "").strip() or _slug(name) or secrets.token_hex(4)
        if menu_id in seen_ids:
            errors.append(f"Menu {index + 1}: duplicate id `{menu_id}`.")
        seen_ids.add(menu_id)
        try:
            color = _parse_color(data.get("color"))
        except ValueError:
            errors.append(f"Menu {index + 1}: color must be a hex value like #5865F2.")
            color = DEFAULT_COLOR
        result.append(
            {
                "id": menu_id,
                "name": name or menu_id,
                "guild_id": str(guild.id),
                "channel_id": _id_or_none(data.get("channel")),
                "message_id": _id_or_none(data.get("message_id")),
                "embed": {
                    "title": (data.get("title") or "").strip(),
                    "description": (data.get("description") or "").strip(),
                    "color": color,
                    "footer": (data.get("footer") or "").strip(),
                },
                "roles": role_specs,
            }
        )
    return result, errors


def apply_form(bot, guild: discord.Guild, form) -> list[str]:
    config = bot.guild_config(guild.id)
    config["kick_channel_id"] = _id_or_none(form.get("kick_channel"))
    config["kick_action"] = "ban" if form.get("kick_action") == "ban" else "kick"
    config["log_channel_id"] = _id_or_none(form.get("log_channel"))
    config["admin_roles"] = _id_list(form.getall("admin_roles", []))
    config["kick_exempt_roles"] = _id_list(form.getall("kick_exempt_roles", []))

    join = config.setdefault("join", {})
    join["base_role_id"] = _id_or_none(form.get("base_role"))
    invites, invite_errors = _parse_invites(form)
    join["invites"] = invites

    config.setdefault("easter_eggs", {})["enabled"] = form.get("easter_enabled") == "on"

    menus, menu_errors = _parse_menus(form, guild)
    errors = invite_errors + menu_errors
    if not menu_errors:
        config["role_menus"] = menus

    bot.save_config()
    return errors
