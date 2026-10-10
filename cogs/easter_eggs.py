import logging

import discord
from discord.ext import commands

log = logging.getLogger("rolebot.easter_eggs")

GRIEVOUS_GIF = "https://klipy.com/gifs/star-wars-general-grievous-9"


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
        if "hello there" in content:
            await self._greet(message)
        if "sand" in content:
            try:
                await message.reply("I don't like sand.", mention_author=False)
            except discord.HTTPException:
                log.warning("Could not reply with the sand easter egg in %s", message.channel, exc_info=True)

    async def _greet(self, message: discord.Message) -> None:
        easter_eggs = self.bot.guild_config(message.guild.id).get("easter_eggs") or {}
        gif_url = easter_eggs.get("hello_there_gif") or GRIEVOUS_GIF
        try:
            await message.reply(gif_url, mention_author=False)
        except discord.HTTPException:
            log.warning("Could not reply with the hello-there easter egg in %s", message.channel, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EasterEggs(bot))