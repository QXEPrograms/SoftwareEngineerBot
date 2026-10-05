from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, ERROR, Card, fail, reply, send_log, staff_only


def moderation_problem(interaction: discord.Interaction, member: discord.Member):
    if member == interaction.user:
        return "You can't do that to yourself."
    if member.id == interaction.guild.owner_id:
        return "You can't do that to the server owner."
    if interaction.user.id != interaction.guild.owner_id and member.top_role >= interaction.user.top_role:
        return "That member's role is equal to or higher than yours."
    if member.top_role >= interaction.guild.me.top_role:
        return "My role needs to be above that member's role."
    return None


async def notify(member: discord.Member, action: str, reason: str):
    """DM the member before the action, so they can still receive it."""
    try:
        await Card(f"### 🛡️ You were {action}\nYou were **{action}** from **{member.guild.name}**.",
                   None, f"**Reason:** {reason}", banner="moderation", color=ERROR).send(member)
    except discord.HTTPException:
        pass  # DMs closed


async def log_action(interaction: discord.Interaction, title: str, member: discord.Member, reason: str, extra=""):
    await send_log(interaction.guild, Card(
        discord.ui.Section(f"### {title}\n"
                           f"**Member:** {member.mention} (`{member}`)\n"
                           f"**Moderator:** {interaction.user.mention}{extra}\n"
                           f"**Reason:** {reason}",
                           accessory=discord.ui.Thumbnail(member.display_avatar.url)),
        footer=False, color=ERROR))


async def announce_action(interaction: discord.Interaction, title: str, member: discord.Member, reason: str, extra=""):
    await Card(
        discord.ui.Section(f"### {title}\n**{member}**{extra}\n**Reason:** {reason}",
                           accessory=discord.ui.Thumbnail(member.display_avatar.url)),
        f"-# Action by {interaction.user.mention}",
        banner="moderation", color=ERROR,
    ).respond(interaction)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Kick a member")
    @staff_only(kick_members=True)
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await notify(member, "kicked", reason)
        await member.kick(reason=f"{interaction.user}: {reason}")
        await announce_action(interaction, "👢 Member Kicked", member, reason)
        await log_action(interaction, "👢 Member Kicked", member, reason)

    @app_commands.command(description="Ban a member")
    @staff_only(ban_members=True)
    @app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await notify(member, "banned", reason)
        await member.ban(reason=f"{interaction.user}: {reason}")
        await announce_action(interaction, "🔨 Member Banned", member, reason)
        await log_action(interaction, "🔨 Member Banned", member, reason)

    @app_commands.command(description="Timeout a member")
    @app_commands.describe(minutes="Up to 40320 (28 days)")
    @staff_only(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await member.timeout(timedelta(minutes=minutes), reason=f"{interaction.user}: {reason}")
        await announce_action(interaction, "⏳ Member Timed Out", member, reason, f" for **{minutes} min**")
        await log_action(interaction, "⏳ Member Timed Out", member, reason, f"\n**Duration:** {minutes} min")

    @app_commands.command(description="Delete recent messages in this channel")
    @staff_only(manage_messages=True)
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await reply(interaction, description=f"🧹 Deleted **{len(deleted)}** messages.", color=ACCENT, ephemeral=True)
        await send_log(interaction.guild, Card(
            f"### 🧹 Messages Purged\n**Channel:** {interaction.channel.mention}\n"
            f"**Moderator:** {interaction.user.mention}\n**Deleted:** {len(deleted)}",
            footer=False, color=ERROR))


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
