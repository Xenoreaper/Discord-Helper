import logging
from typing import NamedTuple

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.easter_eggs")

GRIEVOUS_GIF = "https://klipy.com/gifs/star-wars-general-grievous-9"
DRG_GOLD_GIF = "https://klipy.com/gifs/rich-compressed-gold"


class EasterEgg(NamedTuple):
    trigger: str
    response: str
    config_key: str | None = None


EASTER_EGGS = (
    EasterEgg("hello there", GRIEVOUS_GIF, config_key="hello_there_gif"),
    EasterEgg("sand", "I don't like sand."),
    EasterEgg("gold", DRG_GOLD_GIF, config_key="were_rich_gif"),
)


class EasterEggs(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if getattr(message, "is_system", lambda: False)() or message.author.bot:
            return

        easter_eggs = self.bot.guild_config(message.guild.id).get("easter_eggs") or {}
        if not easter_eggs.get("enabled", True):
            return

        content = (message.content or "").lower()
        for egg in EASTER_EGGS:
            if egg.trigger in content:
                await self._respond(message, egg, easter_eggs)

    async def _respond(self, message: discord.Message, egg: EasterEgg, config: dict) -> None:
        response = egg.response
        if egg.config_key:
            response = config.get(egg.config_key) or response
        try:
            await message.reply(response, mention_author=False)
        except discord.HTTPException:
            log.warning("Could not send the %r easter egg in %s", egg.trigger, message.channel, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EasterEggs(bot))
