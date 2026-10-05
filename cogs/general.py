import random

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, BRAND_NAME, Card, fail, get_setting, log, make_embed, reply, set_setting

EIGHTBALL = ["Yes.", "No.", "Maybe.", "Definitely!", "Ask again later.", "Very doubtful.", "Without a doubt.",
             "Signs point to yes.", "Don't count on it."]


class General(commands.Cog):
    config = app_commands.Group(name="config", description="Server settings for the bot", guild_only=True,
                                default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- Info ----------
    @app_commands.command(name="help", description="See everything this bot can do")
    async def help_command(self, interaction: discord.Interaction):
        await Card(
            discord.ui.Section(f"## {BRAND_NAME} Bot\nHere's everything I can do for your server.",
                               accessory=discord.ui.Thumbnail(self.bot.user.display_avatar.url)),
            None,
            "**🔧 General**\n`/ping` `/hello` `/help`",
            "**🎲 Fun**\n`/8ball` `/roll` `/coinflip`",
            "**💰 Economy**\n`/daily` `/balance` `/pay` `/leaderboard`\n-# You also earn coins just by chatting!",
            "**📈 Server Stocks**\n`/stock info` `/stock buy` `/stock sell` `/stock portfolio`",
            "**🛡️ Moderation**\n`/kick` `/ban` `/timeout` `/purge`",
            None,
            "**⚙️ Setup** (staff)\n`/panel rules` `/panel tickets` `/stock setup`\n"
            "`/config view` `/config welcome` `/config logs` `/config autorole`",
        ).respond(interaction, ephemeral=True)

    @app_commands.command(description="Check the bot's latency")
    async def ping(self, interaction: discord.Interaction):
        await reply(interaction, description=f"🏓 Pong! **{round(self.bot.latency * 1000)}ms**")

    @app_commands.command(description="Say hello")
    async def hello(self, interaction: discord.Interaction):
        await reply(interaction, description=f"👋 Hello, {interaction.user.mention}!")

    # ---------- Fun ----------
    @app_commands.command(name="8ball", description="Ask the magic 8-ball")
    async def eightball(self, interaction: discord.Interaction, question: str):
        await reply(interaction, "🎱 Magic 8-Ball", f"**Q:** {question}\n**A:** {random.choice(EIGHTBALL)}")

    @app_commands.command(description="Roll a die")
    async def roll(self, interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
        await reply(interaction, description=f"🎲 You rolled **{random.randint(1, sides)}** (d{sides})")

    @app_commands.command(description="Flip a coin")
    async def coinflip(self, interaction: discord.Interaction):
        await reply(interaction, description=f"🪙 **{random.choice(['Heads', 'Tails'])}**!")

    # ---------- Config ----------
    @config.command(name="view", description="See this server's bot settings")
    async def config_view(self, interaction: discord.Interaction):
        gid = interaction.guild_id

        def show(name, kind):
            value = get_setting(gid, name)
            if not value:
                return "Not set"
            return {"channel": f"<#{value}>", "role": f"<@&{value}>"}[kind]

        embed = make_embed("⚙️ Server Settings")
        embed.add_field(name="Welcome channel", value=show("welcome_channel", "channel"))
        embed.add_field(name="Staff log channel", value=show("log_channel", "channel"))
        embed.add_field(name="Auto role", value=show("autorole", "role"))
        embed.add_field(name="Ticket category", value=show("ticket_category", "channel"))
        embed.add_field(name="Ticket support role", value=show("support_role", "role"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="welcome", description="Set the welcome channel (leave empty to turn off)")
    async def config_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        set_setting(interaction.guild_id, "welcome_channel", channel and channel.id)
        text = f"Welcome messages will be sent to {channel.mention}." if channel else "Welcome messages turned off."
        await reply(interaction, description=text, color=ACCENT, ephemeral=True)

    @config.command(name="logs", description="Set the staff log channel for mod actions and ticket transcripts")
    async def config_logs(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        set_setting(interaction.guild_id, "log_channel", channel and channel.id)
        text = f"Staff logs will be sent to {channel.mention}." if channel else "Staff logs turned off."
        await reply(interaction, description=text, color=ACCENT, ephemeral=True)

    @config.command(name="autorole", description="Give new members a role automatically (leave empty to turn off)")
    async def config_autorole(self, interaction: discord.Interaction, role: discord.Role | None = None):
        if role and (role.managed or role.is_default()):
            return await fail(interaction, "That role can't be given out.")
        if role and role >= interaction.guild.me.top_role:
            return await fail(interaction, "My role needs to be above that role in Server Settings → Roles.")
        set_setting(interaction.guild_id, "autorole", role and role.id)
        text = f"New members will get {role.mention}." if role else "Auto role turned off."
        await reply(interaction, description=text, color=ACCENT, ephemeral=True)

    # ---------- Joins ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        role = guild.get_role(get_setting(guild.id, "autorole") or 0)
        if role and not member.bot:
            try:
                await member.add_roles(role, reason="Auto role")
            except discord.HTTPException:
                log.warning("Couldn't give the auto role in %s", guild)

        channel = guild.get_channel(get_setting(guild.id, "welcome_channel") or 0)
        if channel:
            await Card(
                discord.ui.Section(f"## Welcome to {guild.name}!\n"
                                   f"Hey {member.mention}, glad you're here! 🌺\n"
                                   f"You're our **#{guild.member_count:,}** member.",
                                   accessory=discord.ui.Thumbnail(member.display_avatar.url)),
                None,
                "-# Make sure to read the rules, and open a ticket if you ever need help.",
                banner="welcome",
            ).send(channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
