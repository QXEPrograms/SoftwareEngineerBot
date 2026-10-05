import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, Card, fail, get_setting, log, reply, send_log, set_setting
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


def role_problem(guild: discord.Guild, role: discord.Role):
    if role.managed or role.is_default():
        return "That role can't be given out."
    if role >= guild.me.top_role:
        return f"My role needs to be above {role.mention} in Server Settings → Roles."
    return None


class VerifyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify:button")

    async def callback(self, interaction: discord.Interaction):
        guild, member = interaction.guild, interaction.user
        role = guild.get_role(get_setting(guild.id, "verify_role") or 0)
        if not role:
            return await fail(interaction, "Verification isn't set up yet. Ask a staff member to run `/panel verify`.")
        if role in member.roles:
            return await reply(interaction, description=f"✅ You're already verified! Enjoy **{guild.name}**.",
                               color=ACCENT, ephemeral=True)
        if problem := role_problem(guild, role):
            log.warning("Verify failed in %s: %s", guild, problem)
            return await fail(interaction, "I couldn't give you the role. Please let a staff member know.")
        await member.add_roles(role, reason="Verified with the verify button")
        await reply(interaction, "✅ You're verified!",
                    f"Welcome to **{guild.name}**! You now have {role.mention} and can see the rest of the server.",
                    color=ACCENT, ephemeral=True)
        await send_log(guild, Card(
            discord.ui.Section(f"### ✅ Member Verified\n**Member:** {member.mention} (`{member}`)\n"
                               f"**Account created:** {discord.utils.format_dt(member.created_at, 'R')}",
                               accessory=discord.ui.Thumbnail(member.display_avatar.url)),
            footer=False, color=ACCENT))


def verify_panel(guild_name: str) -> Card:
    return Card(
        f"### ✅ Verify to get access\nWelcome to **{guild_name}**! "
        "Click the button below to verify and unlock the rest of the server.",
        None,
        "-# By verifying, you agree to follow the server rules.",
        discord.ui.ActionRow(VerifyButton()),
        banner="verify",
    )


class Panels(commands.Cog):
    panel = app_commands.Group(name="panel", description="Post a branded panel in this channel", guild_only=True,
                               default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(Card(discord.ui.ActionRow(RulesSelect()), footer=False))
        bot.add_view(Card(discord.ui.ActionRow(VerifyButton()), footer=False))

    @panel.command(name="verify", description="Post a verify button that gives new members a role")
    @app_commands.describe(role="The role members get when they verify, e.g. @Member")
    async def panel_verify(self, interaction: discord.Interaction, role: discord.Role):
        if problem := role_problem(interaction.guild, role):
            return await fail(interaction, problem)
        set_setting(interaction.guild_id, "verify_role", role.id)
        await verify_panel(interaction.guild.name).send(interaction.channel)
        await reply(interaction, description=f"Verify panel posted. Members will get {role.mention}.",
                    color=ACCENT, ephemeral=True)

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
