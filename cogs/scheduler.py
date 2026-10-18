import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.reaction_roles import resolve_links

log = logging.getLogger("rolebot.scheduler")

UTC = timezone.utc
VALID_TYPES = ("once", "interval", "daily", "weekly")

_WEEKDAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WEEKDAY_RE = r"(?:mon|tue|wed|thu|fri|sat|sun)"
_WHEN_HELP = (
    "Examples: `in 2h 30m`, `30m`, `daily 14:00`, `14:00`, "
    "`weekly mon,wed 18:00`, `every 30m`, `2026-10-18 14:00`."
)

_RELATIVE = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
_INTERVAL = re.compile(r"every\s+(\d+)\s*(m|h|d)", re.IGNORECASE)
_WEEKLY = re.compile(
    rf"(?:weekly\s+)?({_WEEKDAY_RE}(?:\s*,\s*{_WEEKDAY_RE})*)\s+(\d{{1,2}}):(\d{{2}})",
    re.IGNORECASE,
)
_DAILY = re.compile(r"(?:daily\s+)?(\d{1,2}):(\d{2})", re.IGNORECASE)
_ABS_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d")


def get_timezone(config: dict | None) -> ZoneInfo:
    name = (config or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def _check_time(hour: int, minute: int) -> None:
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"`{hour:02d}:{minute:02d}` isn't a valid time.")


def parse_when(text: str, tz: ZoneInfo, now: datetime) -> tuple[dict, datetime]:
    """Turn a human time into (schedule spec, next run in UTC). Raises ValueError."""
    raw = (text or "").strip()
    low = raw.lower()
    if not raw:
        raise ValueError(f"Tell me when to send it. {_WHEN_HELP}")

    match = _INTERVAL.fullmatch(low)
    if match:
        amount = int(match.group(1))
        minutes = amount * {"m": 1, "h": 60, "d": 1440}[match.group(2)]
        if minutes < 1:
            raise ValueError("The repeat interval must be at least a minute.")
        return {"type": "interval", "minutes": minutes}, now + timedelta(minutes=minutes)

    relative = low[3:].strip() if low.startswith("in ") else low
    parts = _RELATIVE.findall(relative)
    if parts and not _RELATIVE.sub("", relative).strip(" ,"):
        seconds = sum(int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit.lower()] for value, unit in parts)
        if seconds < 1:
            raise ValueError("Give me at least a minute of delay.")
        run = now + timedelta(seconds=seconds)
        return {"type": "once"}, run

    match = _WEEKLY.fullmatch(raw)
    if match:
        days = []
        for part in match.group(1).split(","):
            key = part.strip().lower()[:3]
            if key not in _WEEKDAY_NAMES:
                raise ValueError(f"`{part.strip()}` isn't a weekday — use mon, tue, wed, ...")
            if _WEEKDAY_NAMES[key] not in days:
                days.append(_WEEKDAY_NAMES[key])
        hour, minute = int(match.group(2)), int(match.group(3))
        _check_time(hour, minute)
        days.sort()
        schedule = {"type": "weekly", "days": days, "time": f"{hour:02d}:{minute:02d}"}
        return schedule, next_run_for(schedule, tz, now)

    match = _DAILY.fullmatch(raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        _check_time(hour, minute)
        schedule = {"type": "daily", "time": f"{hour:02d}:{minute:02d}"}
        return schedule, next_run_for(schedule, tz, now)

    for fmt in _ABS_FORMATS:
        try:
            naive = datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        run = naive.astimezone(UTC)
        if run <= now:
            raise ValueError("That time is already in the past.")
        return {"type": "once"}, run

    raise ValueError(f"Couldn't understand that time. {_WHEN_HELP}")


def next_run_for(schedule: dict, tz: ZoneInfo, after: datetime) -> datetime | None:
    """Next run in UTC after `after`, or None for a one-off."""
    kind = schedule.get("type")
    if kind == "interval":
        return after + timedelta(minutes=int(schedule.get("minutes", 1)))
    if kind in ("daily", "weekly"):
        try:
            hour, minute = (int(part) for part in str(schedule.get("time", "")).split(":"))
        except ValueError:
            return None
        local = after.astimezone(tz)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if kind == "daily":
            if candidate <= local:
                candidate += timedelta(days=1)
            return candidate.astimezone(UTC)
        days = {int(day) for day in schedule.get("days", [])}
        if not days:
            return None
        for offset in range(8):
            moment = candidate + timedelta(days=offset)
            if moment > local and moment.weekday() in days:
                return moment.astimezone(UTC)
        return (candidate + timedelta(days=7)).astimezone(UTC)
    return None


def parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _parse_color(value: str) -> int | None:
    text = value.strip().lstrip("#")
    if not text:
        return None
    if len(text) == 6 and all(char in "0123456789abcdefABCDEF" for char in text):
        return int(text, 16)
    if text.isdigit() and int(text) <= 0xFFFFFF:
        return int(text)
    raise ValueError(value)


def describe_entry(entry: dict) -> str:
    when = entry.get("when_raw")
    if when:
        return when
    schedule = entry.get("schedule", {})
    kind = schedule.get("type")
    if kind == "interval":
        return f"every {schedule.get('minutes')}m"
    if kind in ("daily", "weekly"):
        days = ",".join(
            name
            for name, index in sorted(_WEEKDAY_NAMES.items(), key=lambda item: item[1])
            if index in set(schedule.get("days", []))
        )
        prefix = f"{days} " if kind == "weekly" else ""
        return f"{prefix}{schedule.get('time')}"
    return "once"


def new_entry_id(existing: list[dict]) -> str:
    used = {entry.get("id") for entry in existing}
    while True:
        candidate = secrets.token_hex(3)
        if candidate not in used:
            return candidate


def _build_message(entry: dict, guild: discord.Guild | None) -> tuple[str | None, discord.Embed | None]:
    content = entry.get("content") or None
    embed_spec = entry.get("embed") or {}
    title = embed_spec.get("title")
    color = embed_spec.get("color")
    if not title and color is None:
        return resolve_links(guild, content), None
    embed = discord.Embed(
        title=title or None,
        description=resolve_links(guild, content),
        color=discord.Color(int(color)) if color is not None else 0x5865F2,
    )
    return None, embed


async def _schedule_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    entries = interaction.client.guild_config(interaction.guild.id).get("scheduled_messages", [])
    needle = (current or "").lower()
    choices = []
    for entry in entries:
        label = f"{entry.get('id')} · {describe_entry(entry)} · {(entry.get('content') or '')[:40]}"
        if needle and needle not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=str(entry.get("id"))))
        if len(choices) >= 25:
            break
    return choices


def _schedule_embed(bot: commands.Bot, guild: discord.Guild) -> discord.Embed:
    config = bot.guild_config(guild.id)
    entries = config.get("scheduled_messages", [])
    tz_name = config.get("timezone") or "UTC"
    embed = discord.Embed(
        title="Scheduled messages",
        description=f"Timezone: **{tz_name}** (change it in `/settings`).",
        color=0x5865F2,
    )
    if not entries:
        embed.add_field(
            name="No schedules yet",
            value="Use `/schedule create` to add one.",
            inline=False,
        )
        return embed
    lines = []
    for entry in entries[:25]:
        channel = guild.get_channel(int(entry["channel_id"])) if entry.get("channel_id") else None
        channel_text = channel.mention if channel else f"`{entry.get('channel_id')}` (not found)"
        next_run = parse_iso(entry.get("next_run"))
        when = discord.utils.format_dt(next_run, "R") if next_run else "never"
        lines.append(f"`{entry.get('id')}` · {channel_text} · {describe_entry(entry)} · next {when}")
    embed.add_field(name="Schedules", value="\n".join(lines)[:1024], inline=False)
    return embed


class WhenModal(discord.ui.Modal):
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        existing: dict | None = None,
    ) -> None:
        super().__init__(title=("Edit schedule" if existing else "New scheduled message")[:45])
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.existing = existing or {}
        embed_spec = self.existing.get("embed") or {}
        color = embed_spec.get("color")
        self.when_input = discord.ui.TextInput(
            label="When",
            placeholder="in 2h · daily 14:00 · weekly mon 18:00 · every 30m · 2026-10-18 14:00",
            max_length=100,
            default=self.existing.get("when_raw"),
        )
        self.message_input = discord.ui.TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=self.existing.get("content"),
            placeholder="Inline links: type #rules, @Moderator or @alice",
        )
        self.title_input = discord.ui.TextInput(
            label="Embed title (optional)",
            required=False,
            max_length=256,
            default=embed_spec.get("title"),
        )
        self.color_input = discord.ui.TextInput(
            label="Embed color #RRGGBB (optional)",
            required=False,
            max_length=9,
            default=f"#{color:06x}" if isinstance(color, int) else None,
        )
        for item in (self.when_input, self.message_input, self.title_input, self.color_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        config = self.bot.guild_config(self.guild.id)
        tz = get_timezone(config)
        now = datetime.now(UTC)
        try:
            schedule, next_run = parse_when(self.when_input.value, tz, now)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        color_text = self.color_input.value.strip()
        try:
            color = _parse_color(color_text) if color_text else None
        except ValueError:
            await interaction.response.send_message(
                "Color must be like #5865F2 (hex) or a decimal number.", ephemeral=True
            )
            return

        message = self.message_input.value.strip()
        if not message:
            await interaction.response.send_message("The message can't be empty.", ephemeral=True)
            return

        entries = config.setdefault("scheduled_messages", [])
        embed_spec = {"title": self.title_input.value.strip() or None, "color": color}
        if self.existing:
            entry = self.existing
            entry["channel_id"] = str(self.channel.id)
            entry["content"] = message
            entry["embed"] = embed_spec
            entry["when_raw"] = self.when_input.value.strip()
            entry["schedule"] = schedule
            entry["next_run"] = next_run.isoformat()
            action = "updated"
        else:
            entry = {
                "id": new_entry_id(entries),
                "channel_id": str(self.channel.id),
                "content": message,
                "embed": embed_spec,
                "when_raw": self.when_input.value.strip(),
                "schedule": schedule,
                "next_run": next_run.isoformat(),
                "created_by": str(interaction.user.id),
            }
            entries.append(entry)
            action = "created"

        saved = self.bot.save_config()
        note = "" if saved else " (could not write config.json — the change may be lost on restart)"
        await interaction.response.send_message(
            f"Schedule `{entry['id']}` {action} for {self.channel.mention} — next run "
            f"{discord.utils.format_dt(next_run, 'R')}.{note}",
            ephemeral=True,
        )


class DeleteScheduleView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, entry: dict) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.guild = guild
        self.entry = entry

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        entries = self.bot.guild_config(self.guild.id).get("scheduled_messages", [])
        if self.entry in entries:
            entries.remove(self.entry)
            saved = self.bot.save_config()
        else:
            saved = True
        note = "" if saved else " (could not write config.json)"
        await interaction.response.edit_message(
            content=f"Deleted schedule `{self.entry.get('id')}`.{note}", embed=None, view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


class Scheduler(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.check.start()

    def cog_unload(self) -> None:
        self.check.cancel()

    @tasks.loop(seconds=30)
    async def check(self) -> None:
        now = datetime.now(UTC)
        for guild in self.bot.guilds:
            try:
                await self._run_due(guild, now)
            except Exception:
                log.exception("Scheduler failed for guild %s", guild.id)

    @check.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()
        self.ensure_next_runs()

    def ensure_next_runs(self) -> None:
        changed = False
        now = datetime.now(UTC)
        for config in self.bot.configs.values():
            tz = get_timezone(config)
            for entry in config.get("scheduled_messages", []):
                if parse_iso(entry.get("next_run")) is not None:
                    continue
                next_run = next_run_for(entry.get("schedule", {}), tz, now)
                if next_run is not None:
                    entry["next_run"] = next_run.isoformat()
                    changed = True
        if changed:
            self.bot.save_config()

    async def _run_due(self, guild: discord.Guild, now: datetime) -> None:
        config = self.bot.guild_config(guild.id)
        entries = config.get("scheduled_messages", [])
        if not entries:
            return
        tz = get_timezone(config)
        changed = False
        for entry in list(entries):
            next_run = parse_iso(entry.get("next_run"))
            if next_run is None or next_run > now:
                continue
            await self._send(guild, entry)
            following = next_run_for(entry.get("schedule", {}), tz, now)
            if following is None:
                entries.remove(entry)
            else:
                entry["next_run"] = following.isoformat()
            changed = True
        if changed:
            self.bot.save_config()

    async def _send(self, guild: discord.Guild, entry: dict) -> None:
        channel = guild.get_channel(int(entry["channel_id"])) if entry.get("channel_id") else None
        if not isinstance(channel, discord.TextChannel):
            log.warning("Scheduled message %s in %s has no valid channel", entry.get("id"), guild.id)
            return
        content, embed = _build_message(entry, guild)
        try:
            if embed is not None:
                await channel.send(content=content, embed=embed)
            else:
                await channel.send(content=content)
        except discord.HTTPException:
            log.warning("Could not post scheduled message %s in %s", entry.get("id"), guild.id, exc_info=True)

    def _allowed(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and self.bot.is_bot_admin(member)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("This command only works inside a server.", ephemeral=True)
            return False
        if not self._allowed(interaction):
            await interaction.response.send_message(
                "You need the Administrator permission or a bot admin role to use this command.",
                ephemeral=True,
            )
            return False
        return True

    def _find(self, guild: discord.Guild, entry_id: str) -> dict | None:
        return next(
            (entry for entry in self.bot.guild_config(guild.id).get("scheduled_messages", []) if str(entry.get("id")) == entry_id),
            None,
        )

    @app_commands.command(
        name="schedule", description="Create, edit, delete or list scheduled messages"
    )
    @app_commands.guild_only()
    @app_commands.describe(
        action="What to do — leave empty to list schedules",
        channel="Channel to post in (used by create)",
        entry="Which schedule (used by edit/delete)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="create", value="create"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="delete", value="delete"),
        ]
    )
    @app_commands.autocomplete(entry=_schedule_choices)
    async def schedule(
        self,
        interaction: discord.Interaction,
        action: str | None = None,
        channel: discord.TextChannel | None = None,
        entry: str | None = None,
    ) -> None:
        if not await self._guard(interaction):
            return

        action = (action or "list").lower()
        if action == "list":
            await interaction.response.send_message(
                embed=_schedule_embed(self.bot, interaction.guild), ephemeral=True
            )
            return

        if action == "create":
            if channel is None:
                await interaction.response.send_message(
                    "Pick a `channel` for the new schedule.", ephemeral=True
                )
                return
            await interaction.response.send_modal(WhenModal(self.bot, interaction.guild, channel))
            return

        found = self._find(interaction.guild, entry) if entry else None
        if found is None:
            await interaction.response.send_message(
                "Pick a schedule with `entry` (start typing to search).", ephemeral=True
            )
            return
        target = interaction.guild.get_channel(int(found["channel_id"]))
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "The configured channel no longer exists — delete and recreate this schedule.",
                ephemeral=True,
            )
            return

        if action == "edit":
            await interaction.response.send_modal(
                WhenModal(self.bot, interaction.guild, target, existing=found)
            )
            return

        embed = discord.Embed(
            title="Delete this schedule?",
            description=(
                f"`{found.get('id')}` → {target.mention}\nWhen: {describe_entry(found)}"
            ),
            color=0xED4245,
        )
        await interaction.response.send_message(
            embed=embed, view=DeleteScheduleView(self.bot, interaction.guild, found), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scheduler(bot))
