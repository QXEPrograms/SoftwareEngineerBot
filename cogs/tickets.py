import asyncio
import io
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, ERROR, fail, get_setting, log, make_embed, reply, send_log, set_setting

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


class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
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
        embed = make_embed("🎫 Ticket Opened",
                           f"Thanks {user.mention}! Describe what you need and a staff member will be with you shortly.")
        await channel.send(content=" ".join(m.mention for m in (user, support_role) if m), embed=embed,
                           view=TicketClose())
        await reply(interaction, description=f"Your ticket is ready: {channel.mention}", color=ACCENT, ephemeral=True)


class TicketClose(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
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


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketPanel())
        bot.add_view(TicketClose())

    @app_commands.command(description="Post a ticket panel in this channel")
    @app_commands.describe(category="Where new tickets go", support_role="Role that can see and answer tickets")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def ticketpanel(self, interaction: discord.Interaction, category: discord.CategoryChannel | None = None,
                          support_role: discord.Role | None = None):
        if category:
            set_setting(interaction.guild_id, "ticket_category", category.id)
        if support_role:
            set_setting(interaction.guild_id, "support_role", support_role.id)
        embed = make_embed("🎫 Support Tickets", "Need help? Click the button below to open a private ticket with our team.")
        await interaction.channel.send(embed=embed, view=TicketPanel())
        await reply(interaction, description="Ticket panel posted.", color=ACCENT, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
