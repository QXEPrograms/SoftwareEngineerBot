import discord
from discord import app_commands
from discord.ext import commands

import core

EXTENSIONS = ["cogs.general", "cogs.tickets", "cogs.moderation", "cogs.economy", "cogs.announcements"]


class HawaiiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # welcome messages, auto role, leaderboard
        intents.message_content = True  # ticket transcripts
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None,
                         activity=discord.Activity(type=discord.ActivityType.watching, name=core.BRAND_NAME))

    async def setup_hook(self):
        core.footer_icon = self.user.display_avatar.url
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        self.tree.error(on_app_command_error)
        await self.tree.sync()

    async def on_ready(self):
        core.log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
    if isinstance(error, app_commands.MissingPermissions):
        message = "You don't have permission to use this command."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command only works in a server."
    elif isinstance(error, app_commands.CheckFailure):
        message = "Only the bot owner can use this command."
    elif isinstance(original, discord.Forbidden):
        message = "I don't have permission to do that. Make sure my role is high enough in Server Settings → Roles."
    else:
        core.log.exception("Command error", exc_info=original)
        message = "Something went wrong. Please try again."
    await core.fail(interaction, message)


if __name__ == "__main__":
    HawaiiBot().run(core.TOKEN, root_logger=True)
