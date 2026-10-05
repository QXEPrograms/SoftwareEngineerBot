import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from core import ACCENT, Card, fail, get_setting, log, make_public, reply, send_log, set_setting
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
        if get_setting(guild.id, "verify_channel") != interaction.channel_id:
            set_setting(guild.id, "verify_channel", interaction.channel_id)  # so welcome messages can link here
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


def has_custom_id(components, custom_id):
    return any(getattr(c, "custom_id", None) == custom_id or has_custom_id(getattr(c, "children", []), custom_id)
               for c in components)


async def find_verify_panel(guild: discord.Guild) -> discord.TextChannel | None:
    """Find the channel holding a verify panel the bot posted earlier."""
    for channel in guild.text_channels:
        if not channel.permissions_for(guild.me).read_message_history:
            continue
        try:
            async for message in channel.history(limit=30):
                if message.author.id == guild.me.id and has_custom_id(message.components, "verify:button"):
                    return channel
        except discord.HTTPException:
            continue
    return None


class Panels(commands.Cog):
    panel = app_commands.Group(name="panel", description="Post a branded panel in this channel", guild_only=True,
                               default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.checked_setup = False
        bot.add_view(Card(discord.ui.ActionRow(RulesSelect()), footer=False))
        bot.add_view(Card(discord.ui.ActionRow(VerifyButton()), footer=False))

    @commands.Cog.listener()
    async def on_ready(self):
        if self.checked_setup:  # on_ready also fires after reconnects
            return
        self.checked_setup = True
        for guild in self.bot.guilds:
            try:
                await self.fix_verification_setup(guild)
            except discord.HTTPException:
                log.exception("Couldn't check the verification setup in %s", guild)

    async def fix_verification_setup(self, guild: discord.Guild):
        """Make sure unverified members can see the verify and welcome channels."""
        if not guild.get_role(get_setting(guild.id, "verify_role") or 0):
            log.info("Verification isn't set up in %s, skipping channel check", guild)
            return
        changes = []
        verify_channel = guild.get_channel(get_setting(guild.id, "verify_channel") or 0)
        if not verify_channel:
            verify_channel = await find_verify_panel(guild)
            if verify_channel:
                set_setting(guild.id, "verify_channel", verify_channel.id)
                changes.append(f"Found the verify panel in {verify_channel.mention}. Welcome messages now link to it.")
        if verify_channel and await make_public(verify_channel, read_only=True):
            changes.append(f"{verify_channel.mention} is now visible to everyone, read-only.")
        welcome = guild.get_channel(get_setting(guild.id, "welcome_channel") or 0)
        if welcome and await make_public(welcome, read_only=False):
            changes.append(f"{welcome.mention} is now visible to unverified members.")
        log.info("Verification check in %s: verify channel=%s, welcome channel=%s, changes=%s",
                 guild, verify_channel, welcome, changes or "none needed")
        if changes:
            await send_log(guild, Card("### 🔧 Verification Setup Updated\n" + "\n".join(f"• {c}" for c in changes),
                                       footer=False, color=ACCENT))

    @panel.command(name="verify", description="Post a verify button that gives new members a role")
    @app_commands.describe(role="The role members get when they verify, e.g. @Member")
    async def panel_verify(self, interaction: discord.Interaction, role: discord.Role):
        if problem := role_problem(interaction.guild, role):
            return await fail(interaction, problem)
        set_setting(interaction.guild_id, "verify_role", role.id)
        set_setting(interaction.guild_id, "verify_channel", interaction.channel_id)
        await make_public(interaction.channel, read_only=True)
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
