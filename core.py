"""Shared config, database and the branded Card used by every cog."""
import io
import logging
import os
import re
import sqlite3
from datetime import timedelta

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is missing. Add it to .env (or to Railway's Variables tab).")
OWNER_ID = int((os.getenv("OWNER_ID") or "0").strip() or 0)
# Roles that can use every command, including owner-only ones. Comma-separated role IDs.
MANAGER_ROLES = {int(r) for r in (os.getenv("MANAGER_ROLES") or "1245782007078981703").split(",") if r.strip()}

BRAND_NAME = "Hawaii Studio"
BRAND = discord.Color(0x007FFD)   # main logo blue
ACCENT = discord.Color(0x00E9FD)  # logo cyan, used for success
ERROR = discord.Color(0xFF4D6D)
BOOST_PINK = discord.Color(0xF47FFF)  # Discord's Nitro boost color


def is_booster(member) -> bool:
    return getattr(member, "premium_since", None) is not None

log = logging.getLogger("bot")

# ---------- Database ----------
DB_PATH = (os.getenv("DB_PATH") or "").strip() or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
db = sqlite3.connect(DB_PATH)
db.executescript("""
CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS wallets (user_id INTEGER PRIMARY KEY, coins INTEGER DEFAULT 0, last_daily TEXT);
CREATE TABLE IF NOT EXISTS stocks (guild_id INTEGER PRIMARY KEY, total_shares INTEGER, price INTEGER);
CREATE TABLE IF NOT EXISTS holdings (guild_id INTEGER, user_id INTEGER, shares INTEGER, PRIMARY KEY (guild_id, user_id));
CREATE TABLE IF NOT EXISTS scheduled (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, title TEXT,
                                      message TEXT, ping INTEGER, send_at TEXT);
""")

SETTINGS = ("welcome_channel", "ticket_category", "support_role", "log_channel", "autorole", "verify_role",
            "verify_channel")


def _ensure_columns(table, columns):
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, kind in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


_ensure_columns("settings", {name: "INTEGER" for name in SETTINGS})
_ensure_columns("stocks", {"dividend": "INTEGER DEFAULT 0", "last_dividend": "TEXT"})
db.commit()


def get_setting(guild_id, name):
    assert name in SETTINGS
    row = db.execute(f"SELECT {name} FROM settings WHERE guild_id=?", (guild_id,)).fetchone()
    return row[0] if row else None


def set_setting(guild_id, name, value):
    assert name in SETTINGS
    with db:
        db.execute("INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,))
        db.execute(f"UPDATE settings SET {name}=? WHERE guild_id=?", (value, guild_id))


def get_coins(user_id):
    row = db.execute("SELECT coins FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else 0


def add_coins(user_id, amount):
    """Call inside a `with db:` block."""
    db.execute("INSERT OR IGNORE INTO wallets (user_id, coins) VALUES (?, 0)", (user_id,))
    db.execute("UPDATE wallets SET coins = coins + ? WHERE user_id=?", (amount, user_id))


# ---------- Cards (every message the bot sends) ----------
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
NO_PINGS = discord.AllowedMentions.none()


class Card(discord.ui.LayoutView):
    """A branded message: optional banner image, content, then the Hawaii Studio footer strip.

    Each part can be a string (text, markdown allowed), None (a divider line), or any layout item
    such as an ActionRow of buttons or a Section with a thumbnail.
    `attachments` is a list of (filename, bytes) shown as downloadable files.
    Mentions don't ping anyone unless `pings` allows it.
    """

    def __init__(self, *parts, banner: str | None = None, footer=True, color=BRAND,
                 attachments=(), pings: discord.AllowedMentions = NO_PINGS):
        super().__init__(timeout=None)
        self.images = [name for name in (banner, "footer" if footer else None) if name]
        self.attachments = list(attachments)
        self.pings = pings
        container = discord.ui.Container(accent_colour=color)
        if banner:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{banner}.png")))
        for part in parts:
            if part is None:
                container.add_item(discord.ui.Separator())
            elif isinstance(part, str):
                container.add_item(discord.ui.TextDisplay(part))
            else:
                container.add_item(part)
        for filename, _ in self.attachments:
            container.add_item(discord.ui.File(f"attachment://{filename}"))
        if footer:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://footer.png")))
        self.add_item(container)

    def files(self):
        images = [discord.File(os.path.join(ASSETS, f"{name}.png"), filename=f"{name}.png") for name in self.images]
        return images + [discord.File(io.BytesIO(data), filename=name) for name, data in self.attachments]

    async def send(self, target: discord.abc.Messageable):
        return await target.send(view=self, files=self.files(), allowed_mentions=self.pings)

    async def respond(self, interaction: discord.Interaction, ephemeral=False):
        kwargs = dict(view=self, files=self.files(), ephemeral=ephemeral, allowed_mentions=self.pings)
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)


def text(title=None, description=None):
    return "\n".join(part for part in (f"### {title}" if title else None, description) if part)


async def reply(interaction: discord.Interaction, title=None, description=None, *, color=BRAND, ephemeral=False,
                banner=None, pings=NO_PINGS):
    await Card(text(title, description), banner=banner, color=color, pings=pings).respond(interaction, ephemeral)


async def fail(interaction: discord.Interaction, message: str):
    await Card(f"âŒ {message}", footer=False, color=ERROR).respond(interaction, ephemeral=True)


async def send_log(guild: discord.Guild, card: Card):
    """Post to the server's staff log channel, if one is set."""
    channel = guild.get_channel(get_setting(guild.id, "log_channel") or 0)
    if not channel:
        return
    try:
        await card.send(channel)
    except discord.HTTPException:
        log.warning("Couldn't post to the log channel in %s", guild)


async def make_public(channel: discord.TextChannel, *, read_only: bool) -> bool:
    """Let everyone (including unverified members) see a channel. Returns True if anything changed."""
    everyone = channel.guild.default_role
    overwrite = channel.overwrites_for(everyone)
    if overwrite.view_channel and overwrite.read_message_history and (not read_only or overwrite.send_messages is False):
        return False
    overwrite.update(view_channel=True, read_message_history=True)
    if read_only:
        overwrite.update(send_messages=False)
    await channel.set_permissions(everyone, overwrite=overwrite, reason="Visible to unverified members")
    return True


def is_manager(user) -> bool:
    """The bot owner and anyone with a manager role can use every command."""
    return user.id == OWNER_ID or any(role.id in MANAGER_ROLES for role in getattr(user, "roles", []))


def staff_only(**perms):
    """Allow members with these Discord permissions, or anyone with a manager role."""
    def predicate(interaction: discord.Interaction):
        if is_manager(interaction.user):
            return True
        missing = [name for name, value in perms.items() if getattr(interaction.permissions, name) != value]
        if missing:
            raise app_commands.MissingPermissions(missing)
        return True
    return app_commands.check(predicate)


def owner_only():
    return app_commands.check(lambda interaction: is_manager(interaction.user))


def parse_duration(text: str) -> timedelta | None:
    """Turn '1d2h30m', '45m' or '2h' into a timedelta."""
    text = text.lower().replace(" ", "")
    parts = re.findall(r"(\d+)([dhm])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    units = {"d": "days", "h": "hours", "m": "minutes"}
    return sum((timedelta(**{units[u]: int(n)}) for n, u in parts), timedelta())
