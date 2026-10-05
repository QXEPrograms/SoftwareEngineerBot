from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import ACCENT, db, fail, log, make_embed, owner_only, parse_duration, reply


def announcement(title: str, message: str) -> discord.Embed:
    return make_embed(f"📢 {title}", message.replace("\\n", "\n"))


class Announcements(commands.Cog):
    schedule = app_commands.Group(name="schedule", description="(Bot owner) Schedule announcements for later")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.send_scheduled.start()

    def cog_unload(self):
        self.send_scheduled.cancel()

    def find_channel(self, channel_id: str):
        channel = self.bot.get_channel(int(channel_id)) if channel_id.strip().isdigit() else None
        return channel if isinstance(channel, discord.TextChannel) else None

    @app_commands.command(description="(Bot owner) Send an announcement to any channel the bot can see")
    @app_commands.describe(channel_id="Right-click a channel → Copy Channel ID", message="Use \\n for a new line")
    @owner_only()
    async def announce(self, interaction: discord.Interaction, channel_id: str, title: str, message: str,
                       ping_everyone: bool = False):
        channel = self.find_channel(channel_id)
        if not channel:
            return await fail(interaction, "I couldn't find that channel. Make sure I'm in that server and the ID is correct.")
        await channel.send(content="@everyone" if ping_everyone else None, embed=announcement(title, message))
        await reply(interaction, description=f"Sent to **#{channel.name}** in **{channel.guild.name}**.",
                    color=ACCENT, ephemeral=True)

    @app_commands.command(description="(Bot owner) Announce to a channel with this name in EVERY server")
    @app_commands.describe(channel_name="e.g. announcements", message="Use \\n for a new line")
    @owner_only()
    async def announceall(self, interaction: discord.Interaction, channel_name: str, title: str, message: str):
        await interaction.response.defer(ephemeral=True)
        embed = announcement(title, message)
        sent = 0
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=channel_name.lstrip("#"))
            if channel:
                try:
                    await channel.send(embed=embed)
                    sent += 1
                except discord.HTTPException:
                    pass
        await reply(interaction, description=f"Sent to **{sent}** server(s).", color=ACCENT, ephemeral=True)

    # ---------- Scheduled ----------
    @schedule.command(name="add", description="Schedule an announcement")
    @app_commands.describe(channel_id="Right-click a channel → Copy Channel ID",
                           delay="How long from now, e.g. 30m, 2h, 1d12h", message="Use \\n for a new line")
    @owner_only()
    async def schedule_add(self, interaction: discord.Interaction, channel_id: str, delay: str, title: str,
                           message: str, ping_everyone: bool = False):
        channel = self.find_channel(channel_id)
        if not channel:
            return await fail(interaction, "I couldn't find that channel. Make sure I'm in that server and the ID is correct.")
        wait = parse_duration(delay)
        if not wait:
            return await fail(interaction, "I didn't understand that delay. Try something like `30m`, `2h` or `1d12h`.")
        send_at = datetime.now(timezone.utc) + wait
        with db:
            cursor = db.execute("INSERT INTO scheduled (channel_id, title, message, ping, send_at) VALUES (?, ?, ?, ?, ?)",
                                (channel.id, title, message, ping_everyone, send_at.isoformat()))
        await reply(interaction, "🗓️ Announcement Scheduled",
                    f"**#{cursor.lastrowid}** will be sent to **#{channel.name}** in **{channel.guild.name}** "
                    f"{discord.utils.format_dt(send_at, 'F')} ({discord.utils.format_dt(send_at, 'R')}).",
                    color=ACCENT, ephemeral=True)

    @schedule.command(name="list", description="See upcoming scheduled announcements")
    @owner_only()
    async def schedule_list(self, interaction: discord.Interaction):
        rows = db.execute("SELECT id, channel_id, title, send_at FROM scheduled ORDER BY send_at").fetchall()
        if not rows:
            return await reply(interaction, description="No announcements are scheduled.", ephemeral=True)
        lines = [f"**#{sid}** · <#{cid}> · {discord.utils.format_dt(datetime.fromisoformat(at), 'R')}\n{title}"
                 for sid, cid, title, at in rows[:20]]
        await reply(interaction, "🗓️ Scheduled Announcements", "\n\n".join(lines), ephemeral=True)

    @schedule.command(name="cancel", description="Cancel a scheduled announcement")
    @app_commands.describe(announcement_id="The number from /schedule list")
    @owner_only()
    async def schedule_cancel(self, interaction: discord.Interaction, announcement_id: int):
        with db:
            deleted = db.execute("DELETE FROM scheduled WHERE id=?", (announcement_id,)).rowcount
        if not deleted:
            return await fail(interaction, f"There's no scheduled announcement **#{announcement_id}**.")
        await reply(interaction, description=f"Cancelled announcement **#{announcement_id}**.", color=ACCENT, ephemeral=True)

    @tasks.loop(seconds=30)
    async def send_scheduled(self):
        now = datetime.now(timezone.utc).isoformat()
        due = db.execute("SELECT id, channel_id, title, message, ping FROM scheduled WHERE send_at <= ?", (now,)).fetchall()
        for sid, channel_id, title, message, ping in due:
            with db:
                db.execute("DELETE FROM scheduled WHERE id=?", (sid,))
            channel = self.bot.get_channel(channel_id)
            if not channel:
                log.warning("Scheduled announcement #%d: channel %d not found", sid, channel_id)
                continue
            try:
                await channel.send(content="@everyone" if ping else None, embed=announcement(title, message))
            except discord.HTTPException:
                log.warning("Scheduled announcement #%d couldn't be sent", sid)

    @send_scheduled.before_loop
    async def before_scheduled(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Announcements(bot))
