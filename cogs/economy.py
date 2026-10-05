import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, Card, add_coins, db, fail, get_coins, is_booster, reply

DAILY_COINS = 100
CHAT_COINS = 5        # coins per message...
CHAT_COOLDOWN = 60    # ...at most once per this many seconds
BOOSTER_COINS = 2     # Server Boosters earn 2x daily and chat coins
class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_chat_reward: dict[int, float] = {}

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
            add_coins(message.author.id, CHAT_COINS * (BOOSTER_COINS if is_booster(message.author) else 1))

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
        booster = is_booster(interaction.user)
        amount = DAILY_COINS * (BOOSTER_COINS if booster else 1)
        with db:
            add_coins(uid, amount)
            db.execute("UPDATE wallets SET last_daily=? WHERE user_id=?", (now.isoformat(), uid))
        await reply(interaction, "🎁 Daily Reward",
                    f"You claimed **{amount}** coins!{' 💎 *2× booster bonus*' if booster else ''}\n"
                    f"**Balance:** {get_coins(uid):,} coins\n"
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
                   "-# Earn coins with `/daily` or just by chatting.",
                   banner="leaderboard").respond(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
