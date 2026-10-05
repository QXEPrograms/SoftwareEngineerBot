import asyncio
import io
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core import ACCENT, ERROR, Card, fail, get_setting, log, make_embed, reply, send_log

closing: set[int] = set()  # ticket channels currently being closed


async def build_transcript(channel: discord.TextChannel) -> tuple[str, int]:
    lines = [f"Transcript of #{channel.name} — {channel.guild.name}",
             f"Saved {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    count = 0
    async for message in channel.history(limit=None, oldest_first=True):
        count += 1
        text = message.content
        for embed in message.embeds:
            text += " [embed] " + " — ".join(filter(None, [embed.title, embed.description]))
        for attachment in message.attachments:
            text += f" [file] {attachment.url}"
        lines.append(f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author}: {text.strip()}")
    return "\n".join(lines), count


class OpenTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:open")

    async def callback(self, interaction: discord.Interaction):
        guild, user = interaction.guild, interaction.user
        topic = f"ticket:{user.id}"
        existing = discord.utils.get(guild.text_channels, topic=topic)
        if existing:
            return await fail(interaction, f"You already have an open ticket: {existing.mention}")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                                  read_message_history=True),
        }
        support_role = guild.get_role(get_setting(guild.id, "support_role") or 0)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            f"ticket-{user.name}", topic=topic, overwrites=overwrites,
            category=guild.get_channel(get_setting(guild.id, "ticket_category") or 0))
        await ticket_opened(user, support_role).send(channel)
        await reply(interaction, description=f"Your ticket is ready: {channel.mention}", color=ACCENT, ephemeral=True)


class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if channel.id in closing:
            return await fail(interaction, "This ticket is already closing.")
        closing.add(channel.id)
        try:
            await reply(interaction, description=f"🔒 Ticket closed by {interaction.user.mention}. "
                                                 "Saving transcript and deleting in 5 seconds...")
            transcript, count = await build_transcript(channel)
            opener_id = int(channel.topic.split(":")[1]) if channel.topic and channel.topic.startswith("ticket:") else None
            filename = f"{channel.name}.txt"

            embed = make_embed("🎫 Ticket Closed", color=ERROR)
            embed.add_field(name="Ticket", value=channel.name)
            embed.add_field(name="Opened by", value=f"<@{opener_id}>" if opener_id else "Unknown")
            embed.add_field(name="Closed by", value=interaction.user.mention)
            embed.add_field(name="Messages", value=str(count))
            await send_log(interaction.guild, embed, discord.File(io.BytesIO(transcript.encode()), filename))

            opener = interaction.guild.get_member(opener_id) if opener_id else None
            if opener:
                try:
                    await opener.send(embed=make_embed("🎫 Your ticket was closed",
                                                       f"Here's a copy of your ticket in **{interaction.guild.name}**."),
                                      file=discord.File(io.BytesIO(transcript.encode()), filename))
                except discord.HTTPException:
                    pass  # DMs closed

            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            log.exception("Failed to close ticket %s", channel)
        finally:
            closing.discard(channel.id)


def ticket_panel() -> Card:
    return Card(
        "### 🎫 Need help?\nClick the button below to open a **private ticket** with our team.",
        None,
        "**Before you open a ticket**\n"
        "• Explain your issue clearly\n"
        "• Include screenshots if you can\n"
        "• Be patient — we'll reply as soon as possible",
        discord.ui.ActionRow(OpenTicketButton()),
        banner="support",
    )


def ticket_opened(user: discord.Member, support_role: discord.Role | None) -> Card:
    who = support_role.mention if support_role else "A staff member"
    return Card(
        f"### 🎫 Ticket Opened\nThanks {user.mention}! Tell us what you need and {who} will be with you shortly.",
        None,
        "-# When your issue is solved, close the ticket below. A transcript will be saved.",
        discord.ui.ActionRow(CloseTicketButton()),
    )


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Re-register the buttons so panels keep working after restarts
        bot.add_view(Card(discord.ui.ActionRow(OpenTicketButton()), footer=False))
        bot.add_view(Card(discord.ui.ActionRow(CloseTicketButton()), footer=False))


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
