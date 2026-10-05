from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, ERROR, fail, make_embed, reply, send_log


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
        await member.send(embed=make_embed(description=f"You were **{action}** from **{member.guild.name}**.\n"
                                                       f"**Reason:** {reason}", color=ERROR))
    except discord.HTTPException:
        pass  # DMs closed


async def log_action(interaction: discord.Interaction, title: str, member: discord.Member, reason: str, extra=None):
    embed = make_embed(title, color=ERROR)
    embed.add_field(name="Member", value=f"{member.mention}\n`{member}`")
    embed.add_field(name="Moderator", value=interaction.user.mention)
    if extra:
        embed.add_field(name=extra[0], value=extra[1])
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await send_log(interaction.guild, embed)


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Kick a member")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await notify(member, "kicked", reason)
        await member.kick(reason=f"{interaction.user}: {reason}")
        await reply(interaction, "👢 Member Kicked", f"**{member}** was kicked.\n**Reason:** {reason}")
        await log_action(interaction, "👢 Member Kicked", member, reason)

    @app_commands.command(description="Ban a member")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await notify(member, "banned", reason)
        await member.ban(reason=f"{interaction.user}: {reason}")
        await reply(interaction, "🔨 Member Banned", f"**{member}** was banned.\n**Reason:** {reason}")
        await log_action(interaction, "🔨 Member Banned", member, reason)

    @app_commands.command(description="Timeout a member")
    @app_commands.describe(minutes="Up to 40320 (28 days)")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason given"):
        if problem := moderation_problem(interaction, member):
            return await fail(interaction, problem)
        await member.timeout(timedelta(minutes=minutes), reason=f"{interaction.user}: {reason}")
        await reply(interaction, "⏳ Member Timed Out",
                    f"**{member}** was timed out for **{minutes} min**.\n**Reason:** {reason}")
        await log_action(interaction, "⏳ Member Timed Out", member, reason, ("Duration", f"{minutes} min"))

    @app_commands.command(description="Delete recent messages in this channel")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await reply(interaction, description=f"🧹 Deleted **{len(deleted)}** messages.", color=ACCENT, ephemeral=True)
        embed = make_embed("🧹 Messages Purged", color=ERROR)
        embed.add_field(name="Channel", value=interaction.channel.mention)
        embed.add_field(name="Moderator", value=interaction.user.mention)
        embed.add_field(name="Deleted", value=str(len(deleted)))
        await send_log(interaction.guild, embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
