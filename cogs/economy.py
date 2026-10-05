import math
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import ACCENT, Card, add_coins, db, fail, get_coins, log, reply, send_log

DAILY_COINS = 100
CHAT_COINS = 5        # coins per message...
CHAT_COOLDOWN = 60    # ...at most once per this many seconds
NO_STOCK = "This server hasn't set up stocks yet. An admin can use `/stock setup`."


# ---------- Stock pricing ----------
# Each share costs more as more are sold: price = base * (1 + sold / total),
# so the price doubles once every share is owned and falls again as people sell.
def share_price(base, sold, total):
    return base * (1 + sold / total)


def trade_value(base, sold, total, amount):
    """Exact coins for `amount` shares when `sold` shares are already owned (sum of each share's price).
    Buying rounds up and selling rounds down, so splitting trades can never make free coins."""
    return base * (amount + (amount * sold + amount * (amount - 1) / 2) / total)


def buy_cost(base, sold, total, amount):
    return math.ceil(trade_value(base, sold, total, amount))


def sell_value(base, sold, total, amount):
    return math.floor(trade_value(base, sold - amount, total, amount))


def get_stock(guild_id):
    return db.execute("SELECT total_shares, price, dividend FROM stocks WHERE guild_id=?", (guild_id,)).fetchone()


def shares_sold(guild_id):
    return db.execute("SELECT COALESCE(SUM(shares), 0) FROM holdings WHERE guild_id=?", (guild_id,)).fetchone()[0]


def user_shares(guild_id, user_id):
    row = db.execute("SELECT shares FROM holdings WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    return row[0] if row else 0


class Economy(commands.Cog):
    stock = app_commands.Group(name="stock", description="Buy ownership shares of this server", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_chat_reward: dict[int, float] = {}
        self.pay_dividends.start()

    def cog_unload(self):
        self.pay_dividends.cancel()

    # ---------- Coins ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        now = time.monotonic()
        if now - self.last_chat_reward.get(message.author.id, -CHAT_COOLDOWN) < CHAT_COOLDOWN:
            return
        self.last_chat_reward[message.author.id] = now
        with db:
            add_coins(message.author.id, CHAT_COINS)

    @app_commands.command(description="Check your (or someone's) coin balance")
    async def balance(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        await Card(discord.ui.Section(f"### 💰 Wallet\n{member.mention} has **{get_coins(member.id):,}** coins.",
                                      accessory=discord.ui.Thumbnail(member.display_avatar.url)),
                   "-# Earn more with `/daily` or just by chatting!").respond(interaction)

    @app_commands.command(description="Claim your daily coins")
    async def daily(self, interaction: discord.Interaction):
        uid = interaction.user.id
        row = db.execute("SELECT last_daily FROM wallets WHERE user_id=?", (uid,)).fetchone()
        now = datetime.now(timezone.utc)
        if row and row[0]:
            next_claim = datetime.fromisoformat(row[0]) + timedelta(hours=24)
            if now < next_claim:
                return await fail(interaction, f"You already claimed today. Come back {discord.utils.format_dt(next_claim, 'R')}.")
        with db:
            add_coins(uid, DAILY_COINS)
            db.execute("UPDATE wallets SET last_daily=? WHERE user_id=?", (now.isoformat(), uid))
        await reply(interaction, "🎁 Daily Reward",
                    f"You claimed **{DAILY_COINS}** coins!\n**Balance:** {get_coins(uid):,} coins\n"
                    f"-# Come back {discord.utils.format_dt(now + timedelta(hours=24), 'R')} for more.", color=ACCENT)

    @app_commands.command(description="Send coins to another member")
    @app_commands.guild_only()
    async def pay(self, interaction: discord.Interaction, member: discord.Member,
                  amount: app_commands.Range[int, 1, 100_000_000]):
        if member == interaction.user or member.bot:
            return await fail(interaction, "You can't pay yourself or a bot.")
        if get_coins(interaction.user.id) < amount:
            return await fail(interaction, f"You only have **{get_coins(interaction.user.id):,}** coins.")
        with db:
            add_coins(interaction.user.id, -amount)
            add_coins(member.id, amount)
        await reply(interaction, "💸 Payment Sent", f"{interaction.user.mention} sent **{amount:,}** coins to {member.mention}.",
                    color=ACCENT, pings=discord.AllowedMentions(users=[member]))

    @app_commands.command(description="See the richest members in this server")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        top = []
        for uid, coins in db.execute("SELECT user_id, coins FROM wallets WHERE coins > 0 ORDER BY coins DESC"):
            if interaction.guild.get_member(uid):
                top.append((uid, coins))
                if len(top) == 10:
                    break
        if not top:
            return await fail(interaction, "Nobody has any coins yet. Try `/daily`!")
        medals = ["🥇", "🥈", "🥉"] + [f"`{n}.`" for n in range(4, 11)]
        lines = [f"{medals[i]} <@{uid}> — **{coins:,}** coins" for i, (uid, coins) in enumerate(top)]
        await Card(f"## 🏆 {interaction.guild.name} Leaderboard", None, "\n".join(lines),
                   "-# Earn coins with `/daily`, by chatting, or from stock dividends.",
                   banner="leaderboard").respond(interaction)

    # ---------- Stocks ----------
    @stock.command(description="(Admin) Offer shares of this server")
    @app_commands.describe(total_shares="How many shares exist", price="Starting price per share (doubles when sold out)",
                           dividend="Coins paid per share to owners every day (0 = none)")
    @app_commands.checks.has_permissions(administrator=True)  # default_permissions doesn't apply to subcommands
    async def setup(self, interaction: discord.Interaction, total_shares: app_commands.Range[int, 1, 1_000_000],
                    price: app_commands.Range[int, 1, 1_000_000], dividend: app_commands.Range[int, 0, 1_000_000] = 0):
        if total_shares < shares_sold(interaction.guild_id):
            return await fail(interaction, "You can't set fewer shares than have already been sold.")
        with db:
            db.execute("""INSERT INTO stocks (guild_id, total_shares, price, dividend) VALUES (?, ?, ?, ?)
                          ON CONFLICT(guild_id) DO UPDATE SET total_shares=excluded.total_shares,
                          price=excluded.price, dividend=excluded.dividend""",
                       (interaction.guild_id, total_shares, price, dividend))
        text = f"**{interaction.guild.name}** now has **{total_shares:,}** shares starting at **{price:,}** coins."
        if dividend:
            text += f"\nOwners earn **{dividend:,}** coins per share every day."
        await reply(interaction, "📈 Stocks Updated", text)

    @stock.command(description="See the share price, availability and top owners")
    async def info(self, interaction: discord.Interaction):
        row = get_stock(interaction.guild_id)
        if not row:
            return await fail(interaction, NO_STOCK)
        total, base, dividend = row
        sold = shares_sold(interaction.guild_id)
        top = db.execute("SELECT user_id, shares FROM holdings WHERE guild_id=? ORDER BY shares DESC LIMIT 5",
                         (interaction.guild_id,)).fetchall()
        medals = ["🥇", "🥈", "🥉", "`4.`", "`5.`"]
        owners = "\n".join(f"{medals[i]} <@{uid}> — **{s:,}** shares ({s / total:.1%})" for i, (uid, s) in enumerate(top))
        await Card(
            f"## 📈 {interaction.guild.name} Stock",
            f"**💵 Price:** {round(share_price(base, sold, total)):,} coins per share\n"
            f"**📦 Available:** {total - sold:,} of {total:,} shares ({sold / total:.1%} owned)\n"
            f"**🏦 Market value:** {round(trade_value(base, 0, total, sold)):,} coins\n"
            f"**💰 Daily dividend:** {f'{dividend:,} coins per share' if dividend else 'None'}",
            None,
            f"**🏆 Top owners**\n{owners or 'Nobody owns shares yet — be the first!'}",
            "-# Buy with `/stock buy` · Sell with `/stock sell` · Check yours with `/stock portfolio`",
            banner="stocks",
        ).respond(interaction)

    @stock.command(description="Buy shares of this server")
    async def buy(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]):
        gid, uid = interaction.guild_id, interaction.user.id
        row = get_stock(gid)
        if not row:
            return await fail(interaction, NO_STOCK)
        total, base, _ = row
        sold = shares_sold(gid)
        if amount > total - sold:
            return await fail(interaction, f"Only **{total - sold:,}** shares are available.")
        cost, coins = buy_cost(base, sold, total, amount), get_coins(uid)
        if coins < cost:
            return await fail(interaction, f"That costs **{cost:,}** coins but you only have **{coins:,}**.")
        with db:
            add_coins(uid, -cost)
            db.execute("INSERT INTO holdings VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET shares = shares + ?",
                       (gid, uid, amount, amount))
        owned = user_shares(gid, uid)
        await reply(interaction, "✅ Shares Purchased",
                    f"Bought **{amount:,}** shares for **{cost:,}** coins.\n"
                    f"You now own **{owned:,}** shares — **{owned / total:.1%}** of {interaction.guild.name}.\n"
                    f"New price: **{round(share_price(base, sold + amount, total)):,}** coins", color=ACCENT)

    @stock.command(description="Sell your shares back")
    async def sell(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]):
        gid, uid = interaction.guild_id, interaction.user.id
        row = get_stock(gid)
        if not row:
            return await fail(interaction, NO_STOCK)
        if user_shares(gid, uid) < amount:
            return await fail(interaction, "You don't own that many shares.")
        total, base, _ = row
        sold = shares_sold(gid)
        earned = sell_value(base, sold, total, amount)
        with db:
            db.execute("UPDATE holdings SET shares = shares - ? WHERE guild_id=? AND user_id=?", (amount, gid, uid))
            db.execute("DELETE FROM holdings WHERE guild_id=? AND user_id=? AND shares <= 0", (gid, uid))
            add_coins(uid, earned)
        await reply(interaction, "💸 Shares Sold",
                    f"Sold **{amount:,}** shares for **{earned:,}** coins.\n"
                    f"New price: **{round(share_price(base, sold - amount, total)):,}** coins", color=ACCENT)

    @stock.command(description="See your shares in this server")
    async def portfolio(self, interaction: discord.Interaction):
        gid, uid = interaction.guild_id, interaction.user.id
        row = get_stock(gid)
        if not row:
            return await fail(interaction, NO_STOCK)
        total, base, dividend = row
        owned, sold = user_shares(gid, uid), shares_sold(gid)
        text = (f"**Shares:** {owned:,} ({owned / total:.1%} of the server)\n"
                f"**Sell value:** {sell_value(base, sold, total, owned):,} coins\n")
        if dividend:
            text += f"**Daily dividend:** {owned * dividend:,} coins\n"
        text += f"**Wallet:** {get_coins(uid):,} coins"
        await Card(f"## 📊 Your Portfolio\nYour stake in **{interaction.guild.name}**.", None, text,
                   banner="stocks").respond(interaction, ephemeral=True)

    @tasks.loop(minutes=30)
    async def pay_dividends(self):
        now = datetime.now(timezone.utc)
        for gid, dividend, last in db.execute(
                "SELECT guild_id, dividend, last_dividend FROM stocks WHERE dividend > 0").fetchall():
            if last and now - datetime.fromisoformat(last) < timedelta(hours=24):
                continue
            holders = db.execute("SELECT user_id, shares FROM holdings WHERE guild_id=?", (gid,)).fetchall()
            with db:
                if last:  # first run just starts the 24h clock
                    for uid, shares in holders:
                        add_coins(uid, shares * dividend)
                db.execute("UPDATE stocks SET last_dividend=? WHERE guild_id=?", (now.isoformat(), gid))
            guild = self.bot.get_guild(gid)
            if last and holders and guild:
                paid = sum(s for _, s in holders) * dividend
                log.info("Paid %d coins in dividends in %s", paid, guild)
                await send_log(guild, Card(f"### 📈 Dividends Paid\n"
                                           f"Paid **{paid:,}** coins to **{len(holders)}** shareholders.",
                                           footer=False))

    @pay_dividends.before_loop
    async def before_dividends(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
