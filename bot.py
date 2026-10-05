import itertools

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core

EXTENSIONS = ["cogs.security", "cogs.general", "cogs.tickets", "cogs.stocks", "cogs.panels", "cogs.moderation",
              "cogs.economy", "cogs.announcements"]

# The bot's "About Me" on its profile (max 400 characters)
BIO = (
    "🛡️ **Hawaii Studio's official engineer.**\n"
    "Security · Tickets · Stock Market · Moderation · Economy\n\n"
    "On duty 24/7. No days off, no chilling on the job.\n"
    "Type `/help` to see everything I can do. 🌺\n\n"
    "⚡ **Made by Brandon**"
)

# Statuses the bot cycles through. {members} is replaced with the member count.
STATUSES = [
    (discord.ActivityType.playing, "security guard 24/7 🛡️"),
    (discord.ActivityType.watching, "over {members} members 👀"),
    (discord.ActivityType.watching, "for raiders 🚨"),
    (discord.ActivityType.playing, "the stock market 📈"),
    (discord.ActivityType.listening, "/help 🌺"),
    (discord.ActivityType.custom, "⚡ Made by Brandon"),
]


class HawaiiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # welcome messages, auto role, leaderboard, raid detection
        intents.message_content = True  # ticket transcripts, spam and link filters
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None,
                         activity=discord.Game(STATUSES[0][1]))
        self.statuses = itertools.cycle(STATUSES)

    async def setup_hook(self):
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        self.tree.error(on_app_command_error)
        await self.tree.sync()
        await self.update_bio()
        self.rotate_status.start()

    async def update_bio(self):
        try:
            app = await self.application_info()
            if app.description != BIO:
                await app.edit(description=BIO)
                core.log.info("Updated the bot's bio")
        except discord.HTTPException:
            core.log.warning("Couldn't update the bot's bio. Paste it into the Developer Portal's description instead.")

    @tasks.loop(seconds=30)
    async def rotate_status(self):
        kind, text = next(self.statuses)
        text = text.format(members=f"{sum(g.member_count or 0 for g in self.guilds):,}")
        activity = discord.CustomActivity(text) if kind is discord.ActivityType.custom else discord.Activity(
            type=kind, name=text)
        await self.change_presence(activity=activity)

    @rotate_status.before_loop
    async def before_rotate(self):
        await self.wait_until_ready()

    async def on_ready(self):
        core.log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))


async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
    if isinstance(error, app_commands.MissingPermissions):
        message = "You don't have permission to use this command."
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command only works in a server."
    elif isinstance(error, app_commands.CheckFailure):
        message = "Only the bot owner and managers can use this command."
    elif isinstance(original, discord.Forbidden):
        message = "I don't have permission to do that. Make sure my role is high enough in Server Settings → Roles."
    else:
        core.log.exception("Command error", exc_info=original)
        message = "Something went wrong. Please try again."
    await core.fail(interaction, message)


if __name__ == "__main__":
    HawaiiBot().run(core.TOKEN, root_logger=True)
