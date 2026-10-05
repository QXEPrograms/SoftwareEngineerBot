import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, Card, reply, set_setting
from cogs.tickets import ticket_panel

RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules.json")


def load_rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def numbered(rules):
    return "\n\n".join(f"**{i}.** {rule}" for i, rule in enumerate(rules, 1))


class RulesSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=s["name"], value=str(i), emoji=s.get("emoji"),
                                        description=s.get("description"))
                   for i, s in enumerate(load_rules()["sections"])]
        super().__init__(custom_id="rules:select", placeholder="📖 Select a rules category...", options=options)

    async def callback(self, interaction: discord.Interaction):
        sections = load_rules()["sections"]
        index = int(self.values[0])
        if index >= len(sections):
            return await reply(interaction, description="That section no longer exists.", ephemeral=True)
        section = sections[index]
        await Card(f"### {section.get('emoji', '📜')} {section['name']}\n{section.get('description', '')}",
                   None, numbered(section["rules"])).respond(interaction, ephemeral=True)


def rules_panel() -> Card:
    rules = load_rules()
    first = rules["sections"][0]
    return Card(
        f"### {first.get('emoji', '📜')} {first['name']}\n{rules['intro']}",
        None,
        numbered(first["rules"]),
        None,
        discord.ui.ActionRow(RulesSelect()),
        f"-# {rules['note']}",
        banner="rules",
    )


class Panels(commands.Cog):
    panel = app_commands.Group(name="panel", description="Post a branded panel in this channel", guild_only=True,
                               default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(Card(discord.ui.ActionRow(RulesSelect()), footer=False))

    @panel.command(name="rules", description="Post the rules panel with a category dropdown")
    async def panel_rules(self, interaction: discord.Interaction):
        await rules_panel().send(interaction.channel)
        await reply(interaction, description="Rules panel posted.", color=ACCENT, ephemeral=True)

    @panel.command(name="tickets", description="Post the support ticket panel")
    @app_commands.describe(category="Where new tickets go", support_role="Role that can see and answer tickets")
    async def panel_tickets(self, interaction: discord.Interaction, category: discord.CategoryChannel | None = None,
                            support_role: discord.Role | None = None):
        if category:
            set_setting(interaction.guild_id, "ticket_category", category.id)
        if support_role:
            set_setting(interaction.guild_id, "support_role", support_role.id)
        await ticket_panel().send(interaction.channel)
        await reply(interaction, description="Ticket panel posted.", color=ACCENT, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Panels(bot))
