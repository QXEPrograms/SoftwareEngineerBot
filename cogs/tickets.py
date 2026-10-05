import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core import ACCENT, BOOST_PINK, BRAND, ERROR, Card, fail, get_setting, is_booster, log, reply, send_log

closing: set[int] = set()  # ticket channels currently being closed

# Each ticket type asks a few questions (max 5) in a pop-up form before the ticket opens.
TICKET_TYPES = {
    "support": {
        "label": "General Support", "emoji": "🛟", "description": "Questions or help with anything",
        "questions": [
            {"label": "What do you need help with?", "long": True},
        ],
    },
    "report": {
        "label": "Report a Member", "emoji": "🚨", "description": "Report someone breaking the rules",
        "questions": [
            {"label": "Who are you reporting?", "placeholder": "Their username or user ID"},
            {"label": "What happened?", "long": True},
            {"label": "Evidence", "placeholder": "Links to screenshots or videos", "long": True, "required": False},
        ],
    },
    "commission": {
        "label": "Commissions / Orders", "emoji": "🛒", "description": "Hire the studio or buy something",
        "questions": [
            {"label": "What do you need made?", "placeholder": "Map, scripts, UI, models, a full game...",
             "long": True},
            {"label": "Budget", "placeholder": "e.g. 5,000 Robux or $50", "required": False},
            {"label": "Deadline", "placeholder": "e.g. 2 weeks, or no rush", "required": False},
        ],
    },
    "partnership": {
        "label": "Partnership", "emoji": "✨", "description": "Partner with Hawaii Studio",
        "questions": [
            {"label": "Server or group name"},
            {"label": "Member count", "placeholder": "e.g. 2,500"},
            {"label": "Why should we partner?", "long": True},
        ],
    },
    "appeal": {
        "label": "Appeal", "emoji": "⚖️", "description": "Appeal a warning, timeout, or ban",
        "questions": [
            {"label": "What were you punished for?"},
            {"label": "Why should it be removed?", "long": True},
        ],
    },
    # Opened from the stock market panel instead of the ticket panel
    "stock": {
        "label": "Stock Purchase", "emoji": "📈", "description": "Buying pieces of a server", "hidden": True,
        "questions": [],
    },
}


def component_text(components):
    """Text inside the bot's card messages, which have no regular message content."""
    for component in components:
        if getattr(component, "content", None):
            yield component.content
        yield from component_text(getattr(component, "children", []))


async def build_transcript(channel: discord.TextChannel) -> tuple[str, int]:
    lines = [f"Transcript of #{channel.name} — {channel.guild.name}",
             f"Saved {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    count = 0
    async for message in channel.history(limit=None, oldest_first=True):
        count += 1
        text = " ".join([message.content, *component_text(message.components)])
        for embed in message.embeds:
            text += " [embed] " + " — ".join(filter(None, [embed.title, embed.description]))
        for attachment in message.attachments:
            text += f" [file] {attachment.url}"
        lines.append(f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author}: {text.strip()}")
    return "\n".join(lines), count


def open_ticket_of(guild: discord.Guild, user_id: int):
    return next((c for c in guild.text_channels if c.topic and c.topic.startswith(f"ticket:{user_id}")), None)


class TicketForm(discord.ui.Modal):
    def __init__(self, key: str):
        ticket_type = TICKET_TYPES[key]
        super().__init__(title=f"{ticket_type['label']} Ticket"[:45], timeout=900)
        self.key = key
        self.inputs = []
        for q in ticket_type["questions"]:
            field = discord.ui.TextInput(
                placeholder=q.get("placeholder"), required=q.get("required", True),
                style=discord.TextStyle.paragraph if q.get("long") else discord.TextStyle.short,
                max_length=1000 if q.get("long") else 100)
            self.add_item(discord.ui.Label(text=q["label"], component=field))
            self.inputs.append((q["label"], field))

    async def on_submit(self, interaction: discord.Interaction):
        answers = [(label, field.value.strip()) for label, field in self.inputs if field.value.strip()]
        await open_ticket(interaction, self.key, answers)


async def open_ticket(interaction: discord.Interaction, key: str, answers: list[tuple[str, str]], staff_items=()):
    """Open a private ticket channel. `staff_items` are extra buttons shown next to Close Ticket."""
    guild, user = interaction.guild, interaction.user
    if existing := open_ticket_of(guild, user.id):
        return await fail(interaction, f"You already have an open ticket: {existing.mention}")
    await interaction.response.defer(ephemeral=True, thinking=True)

    booster = is_booster(user)
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
        f"{'⭐-' if booster else ''}{key}-{user.name}", topic=f"ticket:{user.id}:{key}", overwrites=overwrites,
        category=guild.get_channel(get_setting(guild.id, "ticket_category") or 0),
        **({"position": 0} if booster else {}))  # boosters' tickets go to the top
    await ticket_opened(user, support_role, key, answers, booster, staff_items).send(channel)
    await reply(interaction, description=f"Your ticket is ready: {channel.mention}", color=ACCENT, ephemeral=True)


class OpenTicketButton(discord.ui.Button):
    def __init__(self, key: str, custom_id: str | None = None):
        ticket_type = TICKET_TYPES[key]
        super().__init__(label="Open", style=discord.ButtonStyle.primary, emoji=ticket_type["emoji"],
                         custom_id=custom_id or f"ticket:open:{key}")
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        if existing := open_ticket_of(interaction.guild, interaction.user.id):
            return await fail(interaction, f"You already have an open ticket: {existing.mention}")
        await interaction.response.send_modal(TicketForm(self.key))


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
            key = channel.topic.split(":")[2] if channel.topic and channel.topic.count(":") >= 2 else None
            kind = f"{TICKET_TYPES[key]['emoji']} {TICKET_TYPES[key]['label']}" if key in TICKET_TYPES else "Ticket"
            attachment = [(f"{channel.name}.txt", transcript.encode())]

            await send_log(interaction.guild, Card(
                f"### 🎫 Ticket Closed\n**Ticket:** {channel.name}\n**Type:** {kind}\n"
                f"**Opened by:** {f'<@{opener_id}>' if opener_id else 'Unknown'}\n"
                f"**Closed by:** {interaction.user.mention}\n**Messages:** {count}",
                footer=False, color=ERROR, attachments=attachment))

            opener = interaction.guild.get_member(opener_id) if opener_id else None
            if opener:
                try:
                    await Card(f"### 🎫 Your ticket was closed\n"
                               f"Thanks for reaching out to **{interaction.guild.name}**! "
                               f"Here's a copy of your conversation.",
                               banner="support", attachments=attachment).send(opener)
                except discord.HTTPException:
                    pass  # DMs closed

            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            log.exception("Failed to close ticket %s", channel)
        finally:
            closing.discard(channel.id)


def ticket_panel() -> Card:
    options = [discord.ui.Section(f"**{t['emoji']} {t['label']}**\n{t['description']}", accessory=OpenTicketButton(key))
               for key, t in TICKET_TYPES.items() if not t.get("hidden")]
    return Card(
        "### 🎫 Need help? Open a ticket.\nPick the option that fits best. You'll answer a few quick questions, "
        "then a **private channel** opens with our team.",
        None,
        *options,
        None,
        "-# 💎 Server Boosters get priority support. · Please don't open tickets as a joke.",
        banner="support",
    )


def ticket_opened(user: discord.Member, support_role: discord.Role | None, key: str,
                  answers: list[tuple[str, str]], booster: bool, staff_items=()) -> Card:
    ticket_type = TICKET_TYPES[key]
    who = support_role.mention if support_role else "a staff member"
    parts = [f"### {ticket_type['emoji']} {ticket_type['label']}\n"
             f"Thanks {user.mention}! Add anything else we should know, and {who} will be with you shortly."]
    if booster:
        parts.append("**⭐ Priority support** — this member is boosting the server.")
    if answers:
        parts += [None, "\n\n".join(f"**{label}**\n{value}" for label, value in answers)]
    parts += [None, "-# When your issue is solved, close the ticket below. A transcript will be saved.",
              discord.ui.ActionRow(CloseTicketButton(), *staff_items)]
    return Card(*parts, color=BOOST_PINK if booster else BRAND,
                pings=discord.AllowedMentions(users=[user], roles=[support_role] if support_role else False))


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Re-register the buttons so panels keep working after restarts
        bot.add_view(ticket_panel())
        bot.add_view(Card(discord.ui.ActionRow(CloseTicketButton()), footer=False))
        # Panels posted before ticket types existed: their single button opens General Support
        bot.add_view(Card(discord.ui.ActionRow(OpenTicketButton("support", custom_id="ticket:open")), footer=False))


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
