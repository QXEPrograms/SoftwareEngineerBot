"""Shared config, database and embed helpers used by every cog."""
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is missing. Add it to .env (or to Railway's Variables tab).")
OWNER_ID = int((os.getenv("OWNER_ID") or "0").strip() or 0)

BRAND_NAME = "Hawaii Studio"
BRAND = discord.Color(0x007FFD)   # main logo blue
ACCENT = discord.Color(0x00E9FD)  # logo cyan, used for success
ERROR = discord.Color(0xFF4D6D)

log = logging.getLogger("bot")
footer_icon = None  # set to the bot's avatar once logged in

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

SETTINGS = ("welcome_channel", "ticket_category", "support_role", "log_channel", "autorole")


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


# ---------- Embeds & replies ----------
def make_embed(title=None, description=None, color=BRAND):
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text=BRAND_NAME, icon_url=footer_icon)
    return embed


async def reply(interaction: discord.Interaction, title=None, description=None, *, color=BRAND, ephemeral=False):
    embed = make_embed(title, description, color)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def fail(interaction: discord.Interaction, message: str):
    await reply(interaction, description=f"❌ {message}", color=ERROR, ephemeral=True)


async def send_log(guild: discord.Guild, embed: discord.Embed, file: discord.File | None = None):
    """Post to the server's staff log channel, if one is set."""
    channel = guild.get_channel(get_setting(guild.id, "log_channel") or 0)
    if not channel:
        return
    try:
        await channel.send(embed=embed, **({"file": file} if file else {}))
    except discord.HTTPException:
        log.warning("Couldn't post to the log channel in %s", guild)


def owner_only():
    return app_commands.check(lambda interaction: interaction.user.id == OWNER_ID)


def parse_duration(text: str) -> timedelta | None:
    """Turn '1d2h30m', '45m' or '2h' into a timedelta."""
    text = text.lower().replace(" ", "")
    parts = re.findall(r"(\d+)([dhm])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    units = {"d": "days", "h": "hours", "m": "minutes"}
    return sum((timedelta(**{units[u]: int(n)}) for n, u in parts), timedelta())
