import sqlite3
from collections import namedtuple
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, Card, db, fail, get_setting, is_manager, log, reply, send_log, set_setting, staff_only
from cogs.tickets import open_ticket, open_ticket_of

MAX_LISTINGS = 8  # keeps the panel within Discord's limits
Listing = namedtuple("Listing", "id guild_id name description price total available emoji")
COLUMNS = ", ".join(Listing._fields)


def get_listings(guild_id: int) -> list[Listing]:
    return [Listing(*row) for row in
            db.execute(f"SELECT {COLUMNS} FROM stock_listings WHERE guild_id=? ORDER BY id", (guild_id,))]


def get_listing(listing_id: int, guild_id: int | None = None) -> Listing | None:
    row = db.execute(f"SELECT {COLUMNS} FROM stock_listings WHERE id=?", (listing_id,)).fetchone()
    listing = Listing(*row) if row else None
    return listing if listing and (guild_id is None or listing.guild_id == guild_id) else None


def owner_count(listing_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM stock_owners WHERE listing_id=? AND pieces > 0", (listing_id,)).fetchone()[0]


# ---------- Market panel ----------
def market_panel(guild: discord.Guild) -> Card:
    listings = get_listings(guild.id)
    parts = ["## 📈 Server Stock Market\nOwn a piece of our servers! Pick one below and click **Buy**. "
             "A private ticket opens where our team finishes your purchase.", None]
    if not listings:
        parts.append("*No servers are selling pieces right now. Check back soon!*")
    for i, listing in enumerate(listings):
        if i:
            parts.append(discord.ui.Separator(visible=False))
        owners = owner_count(listing.id)
        stock = (f"📦 **{listing.available:,} / {listing.total:,}** pieces left" if listing.available
                 else "📦 **Sold out**")
        parts.append(discord.ui.Section(
            f"### {listing.emoji} {listing.name}\n"
            + (f"{listing.description}\n" if listing.description else "")
            + f"💵 **{listing.price}** per piece · {stock} · 👥 {owners} owner{'s' if owners != 1 else ''}",
            accessory=BuyButton(listing.id, sold_out=not listing.available)))
    parts += [None, "-# Purchases are completed with staff in your ticket. · See what you own with `/stock portfolio`"]
    return Card(*parts, banner="stocks")


async def refresh_market(guild: discord.Guild) -> bool:
    """Update the posted market panel after a change. Returns False if no panel is posted."""
    channel = guild.get_channel(get_setting(guild.id, "stock_panel_channel") or 0)
    message_id = get_setting(guild.id, "stock_panel_message")
    if not channel or not message_id:
        return False
    try:
        await channel.get_partial_message(message_id).edit(view=market_panel(guild))
        return True
    except discord.NotFound:  # panel was deleted
        set_setting(guild.id, "stock_panel_message", None)
    except discord.HTTPException:
        log.warning("Couldn't update the stock market panel in %s", guild)
    return False


# ---------- Buying ----------
class BuyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"stock:buy:(?P<id>\d+)"):
    def __init__(self, listing_id: int, sold_out: bool = False):
        super().__init__(discord.ui.Button(
            label="Sold out" if sold_out else "Buy", emoji="🛒", disabled=sold_out,
            style=discord.ButtonStyle.secondary if sold_out else discord.ButtonStyle.success,
            custom_id=f"stock:buy:{listing_id}"))
        self.listing_id = listing_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        listing = get_listing(self.listing_id, interaction.guild_id)
        if not listing:
            return await fail(interaction, "That server isn't for sale anymore.")
        if not listing.available:
            return await fail(interaction, f"**{listing.name}** is sold out.")
        if existing := open_ticket_of(interaction.guild, interaction.user.id):
            return await fail(interaction, f"You already have an open ticket: {existing.mention}")
        await interaction.response.send_modal(BuyForm(listing))


class BuyForm(discord.ui.Modal):
    def __init__(self, listing: Listing):
        super().__init__(title=f"Buy pieces of {listing.name}"[:45], timeout=900)
        self.listing_id = listing.id
        self.amount = discord.ui.TextInput(placeholder=f"1 to {listing.available}", max_length=6)
        self.payment = discord.ui.TextInput(placeholder="e.g. Robux, PayPal, Cash App", required=False, max_length=100)
        self.note = discord.ui.TextInput(style=discord.TextStyle.paragraph, required=False, max_length=500)
        self.add_item(discord.ui.Label(text="How many pieces?", description=f"{listing.price} each",
                                       component=self.amount))
        self.add_item(discord.ui.Label(text="How will you pay?", component=self.payment))
        self.add_item(discord.ui.Label(text="Anything else?", component=self.note))

    async def on_submit(self, interaction: discord.Interaction):
        listing = get_listing(self.listing_id, interaction.guild_id)  # may have changed while the form was open
        if not listing or not listing.available:
            return await fail(interaction, "Sorry, that server just sold out.")
        try:
            pieces = int(self.amount.value.strip().replace(",", ""))
        except ValueError:
            pieces = 0
        if not 1 <= pieces <= listing.available:
            return await fail(interaction, f"Please enter a number from 1 to {listing.available}.")
        answers = [("Server", f"{listing.emoji} {listing.name}"),
                   ("Pieces", f"{pieces:,} × {listing.price}  ({listing.available:,} were available)")]
        if self.payment.value.strip():
            answers.append(("Payment method", self.payment.value.strip()))
        if self.note.value.strip():
            answers.append(("Notes", self.note.value.strip()))
        await open_ticket(interaction, "stock", answers,
                          staff_items=[CompleteSaleButton(listing.id, pieces, interaction.user.id)])


class CompleteSaleButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r"stock:sold:(?P<id>\d+):(?P<pieces>\d+):(?P<buyer>\d+)"):
    def __init__(self, listing_id: int, pieces: int, buyer_id: int):
        super().__init__(discord.ui.Button(label="Complete Sale (staff)", emoji="✅",
                                           style=discord.ButtonStyle.success,
                                           custom_id=f"stock:sold:{listing_id}:{pieces}:{buyer_id}"))
        self.listing_id, self.pieces, self.buyer_id = listing_id, pieces, buyer_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), int(match["pieces"]), int(match["buyer"]))

    async def callback(self, interaction: discord.Interaction):
        if not (is_manager(interaction.user) or interaction.permissions.manage_guild):
            return await fail(interaction, "Only staff can complete a sale, once payment is received.")
        listing = get_listing(self.listing_id, interaction.guild_id)
        if not listing:
            return await fail(interaction, "That server isn't listed anymore.")
        if listing.available < self.pieces:
            return await fail(interaction, f"Only **{listing.available}** pieces are left. Adjust it with "
                                           f"`/stock edit` first, or ask the buyer to open a new ticket.")
        try:
            with db:
                db.execute("INSERT INTO stock_sales VALUES (?, ?, ?, ?, ?)", (
                    interaction.channel_id, listing.id, self.buyer_id, self.pieces,
                    datetime.now(timezone.utc).isoformat()))
                db.execute("UPDATE stock_listings SET available = available - ? WHERE id=?", (self.pieces, listing.id))
                db.execute("INSERT INTO stock_owners VALUES (?, ?, ?) ON CONFLICT(listing_id, user_id) "
                           "DO UPDATE SET pieces = pieces + excluded.pieces", (listing.id, self.buyer_id, self.pieces))
        except sqlite3.IntegrityError:
            return await fail(interaction, "This sale was already completed.")

        owned = db.execute("SELECT pieces FROM stock_owners WHERE listing_id=? AND user_id=?",
                           (listing.id, self.buyer_id)).fetchone()[0]
        await reply(interaction, "✅ Sale Completed",
                    f"<@{self.buyer_id}> bought **{self.pieces:,}** pieces of **{listing.emoji} {listing.name}** "
                    f"and now owns **{owned:,}** of {listing.total:,}. Thank you! 🎉",
                    color=ACCENT, pings=discord.AllowedMentions(users=[discord.Object(self.buyer_id)]))
        await send_log(interaction.guild, Card(
            f"### 📈 Stock Sale\n**Server:** {listing.emoji} {listing.name}\n**Buyer:** <@{self.buyer_id}>\n"
            f"**Pieces:** {self.pieces:,} × {listing.price}\n**Completed by:** {interaction.user.mention}\n"
            f"**Pieces left:** {listing.available - self.pieces:,} / {listing.total:,}",
            footer=False, color=ACCENT))
        await refresh_market(interaction.guild)


# ---------- Commands ----------
async def listing_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=f"{l.name} ({l.available}/{l.total} left)"[:100], value=l.id)
            for l in get_listings(interaction.guild_id) if current.lower() in l.name.lower()][:25]


class Stocks(commands.Cog):
    stock = app_commands.Group(name="stock", description="The server stock market", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_dynamic_items(BuyButton, CompleteSaleButton)

    async def updated(self, interaction: discord.Interaction, text: str):
        if await refresh_market(interaction.guild):
            text += "\nThe market panel was updated."
        else:
            text += "\nPost the market with `/panel stocks`."
        await reply(interaction, description=text, color=ACCENT, ephemeral=True)

    @stock.command(name="add", description="(Staff) List a server's pieces on the market")
    @app_commands.describe(name="Server name", price="Price per piece, e.g. $5 or 500 Robux",
                           pieces="How many pieces are for sale", description="Short description (optional)",
                           emoji="Emoji shown next to the name")
    @staff_only(manage_guild=True)
    async def stock_add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 60],
                        price: app_commands.Range[str, 1, 30], pieces: app_commands.Range[int, 1, 100_000],
                        description: app_commands.Range[str, 1, 200] | None = None,
                        emoji: app_commands.Range[str, 1, 20] = "🏝️"):
        if len(get_listings(interaction.guild_id)) >= MAX_LISTINGS:
            return await fail(interaction, f"The market can show up to {MAX_LISTINGS} servers. Remove one first.")
        with db:
            db.execute("INSERT INTO stock_listings (guild_id, name, description, price, total, available, emoji) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (interaction.guild_id, name, description, price, pieces, pieces, emoji))
        await self.updated(interaction, f"Listed **{emoji} {name}**: {pieces:,} pieces at {price} each.")

    @stock.command(name="edit", description="(Staff) Change a listed server")
    @app_commands.describe(listing="The server to change", pieces_left="Pieces still for sale",
                           total_pieces="Total pieces the server is split into")
    @app_commands.autocomplete(listing=listing_autocomplete)
    @staff_only(manage_guild=True)
    async def stock_edit(self, interaction: discord.Interaction, listing: int,
                         name: app_commands.Range[str, 1, 60] | None = None,
                         price: app_commands.Range[str, 1, 30] | None = None,
                         pieces_left: app_commands.Range[int, 0, 100_000] | None = None,
                         total_pieces: app_commands.Range[int, 1, 100_000] | None = None,
                         description: app_commands.Range[str, 1, 200] | None = None,
                         emoji: app_commands.Range[str, 1, 20] | None = None):
        current = get_listing(listing, interaction.guild_id)
        if not current:
            return await fail(interaction, "Pick a server from the list.")
        new = current._replace(**{k: v for k, v in dict(
            name=name, price=price, available=pieces_left, total=total_pieces,
            description=description, emoji=emoji).items() if v is not None})
        if new.available > new.total:
            return await fail(interaction, f"Pieces left ({new.available}) can't be more than the total ({new.total}).")
        with db:
            db.execute("UPDATE stock_listings SET name=?, description=?, price=?, total=?, available=?, emoji=? "
                       "WHERE id=?", (new.name, new.description, new.price, new.total, new.available, new.emoji,
                                      new.id))
        await self.updated(interaction, f"Updated **{new.emoji} {new.name}**: {new.available:,} / {new.total:,} "
                                        f"pieces left at {new.price} each.")

    @stock.command(name="remove", description="(Staff) Take a server off the market")
    @app_commands.autocomplete(listing=listing_autocomplete)
    @staff_only(manage_guild=True)
    async def stock_remove(self, interaction: discord.Interaction, listing: int):
        current = get_listing(listing, interaction.guild_id)
        if not current:
            return await fail(interaction, "Pick a server from the list.")
        with db:
            db.execute("DELETE FROM stock_listings WHERE id=?", (current.id,))
            db.execute("DELETE FROM stock_owners WHERE listing_id=?", (current.id,))
        await self.updated(interaction, f"Removed **{current.emoji} {current.name}** from the market.")

    @stock.command(name="portfolio", description="See the server pieces you own")
    async def stock_portfolio(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        rows = db.execute("SELECT l.emoji, l.name, o.pieces, l.total FROM stock_owners o "
                          "JOIN stock_listings l ON l.id = o.listing_id "
                          "WHERE o.user_id=? AND l.guild_id=? AND o.pieces > 0 ORDER BY o.pieces DESC",
                          (member.id, interaction.guild_id)).fetchall()
        whose = "You don't" if member == interaction.user else f"{member.mention} doesn't"
        lines = "\n".join(f"{emoji} **{name}** — {pieces:,} of {total:,} pieces ({pieces / total:.1%})"
                          for emoji, name, pieces, total in rows)
        await Card(f"## 📊 {'Your' if member == interaction.user else f'{member.display_name}’s'} Portfolio",
                   None,
                   lines or f"{whose} own any pieces yet. Check out the stock market panel to buy some!",
                   banner="stocks").respond(interaction, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stocks(bot))
