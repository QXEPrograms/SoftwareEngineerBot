"""24/7 server protection: anti-nuke, bot and permission guards, anti-raid, alt blocking, spam and link filters."""
import re
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import ACCENT, ERROR, Card, db, get_setting, is_manager, log, reply, send_log, set_setting, staff_only

# ---------- Settings & limits ----------
DEFAULTS = {"sec_spam": 1, "sec_invites": 1, "sec_links": 1, "sec_mentions": 1, "sec_raid": 1, "sec_nuke": 1,
            "sec_alt_days": 3}
SPAM_MESSAGES, SPAM_SECONDS = 6, 5         # 6 messages in 5 seconds
DUPLICATES, DUPLICATE_SECONDS = 4, 20      # the same message 4 times in 20 seconds
MENTION_LIMIT = 6                          # people + roles mentioned in one message
INVITE_STRIKES, STRIKE_SECONDS = 3, 600    # 3 invite links in 10 minutes = timeout
RAID_JOINS, RAID_SECONDS, RAID_MINUTES = 8, 20, 10
RAID_ACCOUNT_DAYS = 30                     # during a raid, accounts newer than this are kicked
NUKE_ACTIONS, NUKE_SECONDS = 5, 60         # 5 destructive actions in a minute
SPAM_TIMEOUT = timedelta(minutes=10)
SCAM_TIMEOUT = timedelta(hours=1)

INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg)/[\w-]+", re.I)
DOMAIN_RE = re.compile(r"https?://(?:www\.)?([^\s/:]+)", re.I)
OFFICIAL_DOMAINS = ("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.gift", "discord.media",
                    "roblox.com", "steampowered.com", "steamcommunity.com")
LOOKALIKE_RE = re.compile(r"d[i1l!]s[ck][o0]r[dcl]|n[i1]tr[o0]|ste[a4]m-?c[o0]mm?un", re.I)
DANGEROUS = discord.Permissions(administrator=True, manage_guild=True, manage_roles=True, manage_channels=True,
                                ban_members=True, kick_members=True, manage_webhooks=True, mention_everyone=True)


def setting(guild_id: int, name: str) -> int:
    value = get_setting(guild_id, name)
    return DEFAULTS.get(name, 0) if value is None else value


def raid_active(guild_id: int) -> bool:
    return (get_setting(guild_id, "sec_raid_until") or 0) > time.time()


def join_block_reason(member: discord.Member) -> str | None:
    """Why a new member gets removed on join, if they do. Used by the welcome message too."""
    if member.bot:
        return None
    age = discord.utils.utcnow() - member.created_at
    if raid_active(member.guild.id) and age < timedelta(days=RAID_ACCOUNT_DAYS):
        return "raid"
    days = setting(member.guild.id, "sec_alt_days")
    if days and age < timedelta(days=days):
        return "alt"
    return None


def is_scam(text: str) -> bool:
    domains = [d.lower() for d in DOMAIN_RE.findall(text)]
    suspicious = [d for d in domains if not any(d == o or d.endswith("." + o) for o in OFFICIAL_DOMAINS)]
    if not suspicious:
        return False
    lowered = text.lower()
    return (any(LOOKALIKE_RE.search(d) for d in suspicious)
            or ("nitro" in lowered and any(w in lowered for w in ("free", "gift", "claim", "giveaway")))
            or ("steam" in lowered and "gift" in lowered))


def is_exempt(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return member.bot or is_manager(member) or perms.manage_messages or perms.administrator


def duration_text(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    return f"{minutes // 60} hour{'s' * (minutes // 60 != 1)}" if minutes >= 60 else f"{minutes} minutes"


class Security(commands.Cog):
    security = app_commands.Group(name="security", description="Server protection", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.on_duty_since = discord.utils.utcnow()
        self.stats: Counter = Counter()                       # (guild_id, kind) -> count
        self.message_times = defaultdict(deque)               # (guild, user) -> timestamps
        self.recent_messages = defaultdict(deque)             # (guild, user) -> (timestamp, text)
        self.invite_strikes = defaultdict(deque)              # (guild, user) -> timestamps
        self.join_times = defaultdict(deque)                  # guild -> timestamps
        self.nuke_actions = defaultdict(deque)                # (guild, actor) -> timestamps
        self.watch.start()

    def cog_unload(self):
        self.watch.cancel()

    # ---------- Helpers ----------
    def trusted(self, guild: discord.Guild, user) -> bool:
        member = guild.get_member(user.id) or user
        return user.id in (guild.owner_id, getattr(self.bot.user, "id", None)) or is_manager(member)

    async def alert(self, guild: discord.Guild, title: str, body: str, ping_owner=False, member=None):
        """Post to the staff log; serious alerts also ping and DM the server owner."""
        text = f"### {title}\n{body}" + (f"\n<@{guild.owner_id}>" if ping_owner else "")
        content = (discord.ui.Section(text, accessory=discord.ui.Thumbnail(member.display_avatar.url))
                   if member else text)
        pings = discord.AllowedMentions(users=[discord.Object(guild.owner_id)]) if ping_owner else None
        await send_log(guild, Card(content, footer=False, color=ERROR, **({"pings": pings} if pings else {})))
        if ping_owner and guild.owner:
            try:
                await Card(f"### {title}\nIn **{guild.name}**: {body}", banner="security", color=ERROR).send(guild.owner)
            except discord.HTTPException:
                pass

    async def find_actor(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int | None = None):
        """Who just did this, according to the audit log."""
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                recent = discord.utils.utcnow() - entry.created_at < timedelta(seconds=15)
                if recent and (target_id is None or getattr(entry.target, "id", None) == target_id):
                    return entry.user
        except discord.HTTPException:
            log.warning("Can't read the audit log in %s", guild)
        return None

    @staticmethod
    def hit(times: deque, window: float) -> int:
        """Record an event now and return how many happened within the window."""
        now = time.monotonic()
        times.append(now)
        while times and now - times[0] > window:
            times.popleft()
        return len(times)

    # ---------- Messages: spam, mentions, invites, scams ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild and not message.webhook_id and isinstance(message.author, discord.Member) \
                and not is_exempt(message.author):
            await self.check_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.content != after.content and after.guild and isinstance(after.author, discord.Member) \
                and not is_exempt(after.author):
            await self.check_message(after, edited=True)

    async def check_message(self, message: discord.Message, edited=False):
        gid, member, text = message.guild.id, message.author, message.content
        key = (gid, member.id)
        if setting(gid, "sec_links") and is_scam(text):
            return await self.punish(message, "🎣 Scam Link Blocked", SCAM_TIMEOUT,
                                     "Scam links usually come from hacked accounts. Secure your account!")
        if setting(gid, "sec_invites") and INVITE_RE.search(text):
            strikes = self.hit(self.invite_strikes[key], STRIKE_SECONDS)
            if strikes >= INVITE_STRIKES:
                self.invite_strikes[key].clear()
                return await self.punish(message, "🔗 Repeated Invite Links", SPAM_TIMEOUT)
            return await self.punish(message, "🔗 Invite Link Removed", None,
                                     f"No server invites here. Strike {strikes}/{INVITE_STRIKES}.")
        mentioned = len({m.id for m in message.mentions if not m.bot}) + len(message.role_mentions)
        if setting(gid, "sec_mentions") and (mentioned >= MENTION_LIMIT or message.mention_everyone):
            return await self.punish(message, "📣 Mass Mention", SPAM_TIMEOUT)
        if edited or not setting(gid, "sec_spam"):
            return
        if self.hit(self.message_times[key], SPAM_SECONDS) >= SPAM_MESSAGES:
            self.message_times[key].clear()
            return await self.punish(message, "🌊 Message Flooding", SPAM_TIMEOUT, purge=True)
        recent = self.recent_messages[key]
        now = time.monotonic()
        recent.append((now, text.strip().lower()))
        while recent and now - recent[0][0] > DUPLICATE_SECONDS:
            recent.popleft()
        if text.strip() and sum(1 for _, t in recent if t == text.strip().lower()) >= DUPLICATES:
            recent.clear()
            return await self.punish(message, "🔁 Repeated Messages", SPAM_TIMEOUT, purge=True)

    async def punish(self, message: discord.Message, title: str, timeout: timedelta | None, note: str = "",
                     purge=False):
        member, channel = message.author, message.channel
        try:
            if purge:  # clean up everything they just flooded
                await channel.purge(limit=50, after=discord.utils.utcnow() - timedelta(seconds=30),
                                    check=lambda m: m.author.id == member.id)
            else:
                await message.delete()
        except discord.HTTPException:
            pass
        action = "Message deleted"
        if timeout and not member.is_timed_out():
            try:
                await member.timeout(timeout, reason=f"Security: {title}")
                action = f"Timed out for {duration_text(timeout)}"
            except discord.HTTPException:
                action += " (couldn't time out, check my role position)"
        self.stats[(message.guild.id, "messages")] += 1
        try:
            await Card(f"🛡️ {member.mention} — **{title.split(' ', 1)[1]}**. {action}."
                       + (f"\n-# {note}" if note else ""), footer=False, color=ERROR).send(channel, delete_after=10)
        except discord.HTTPException:
            pass
        await self.alert(message.guild, f"🛡️ {title}",
                         f"**Member:** {member.mention} (`{member}`)\n**Channel:** {channel.mention}\n"
                         f"**Action:** {action}\n**Message:** {discord.utils.escape_markdown(message.content[:300])}",
                         member=member)

    # ---------- Joins: raids, alts, bots ----------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if member.bot:
            return await self.check_bot_add(member)
        if setting(guild.id, "sec_raid") and not raid_active(guild.id) \
                and self.hit(self.join_times[guild.id], RAID_SECONDS) >= RAID_JOINS:
            await self.start_raid(guild, skip=member.id)  # this member is handled just below
        if reason := join_block_reason(member):
            await self.remove_new_member(member, reason)

    async def remove_new_member(self, member: discord.Member, reason: str):
        guild = member.guild
        if reason == "raid":
            dm = f"**{guild.name}** is in raid lockdown right now. Please try again in a few minutes."
        else:
            days = setting(guild.id, "sec_alt_days")
            dm = (f"Your account is too new to join **{guild.name}**. Accounts must be at least **{days} days** old. "
                  f"You can join again {discord.utils.format_dt(member.created_at + timedelta(days=days), 'R')}.")
        try:
            await Card(f"### 🛡️ You were removed\n{dm}", banner="security", color=ERROR).send(member)
        except discord.HTTPException:
            pass
        try:
            await member.kick(reason=f"Security: {'raid lockdown' if reason == 'raid' else 'account too new'}")
        except discord.HTTPException:
            return log.warning("Couldn't kick %s in %s", member, guild)
        self.stats[(guild.id, reason)] += 1
        await self.alert(guild, "👶 New Account Removed" if reason == "alt" else "🌊 Raid Account Removed",
                         f"**Member:** {member.mention} (`{member}`)\n"
                         f"**Account created:** {discord.utils.format_dt(member.created_at, 'R')}", member=member)

    async def start_raid(self, guild: discord.Guild, skip: int | None = None):
        set_setting(guild.id, "sec_raid_until", int(time.time() + RAID_MINUTES * 60))
        self.stats[(guild.id, "raids")] += 1
        raised = ""
        if guild.verification_level < discord.VerificationLevel.high:
            set_setting(guild.id, "sec_prev_verification", guild.verification_level.value)
            try:
                await guild.edit(verification_level=discord.VerificationLevel.high, reason="Security: raid detected")
                raised = "\nDiscord verification raised to **High** until it's over."
            except discord.HTTPException:
                pass
        await self.alert(guild, "🚨 Raid Detected",
                         f"**{RAID_JOINS}+ members** joined in {RAID_SECONDS} seconds. Raid mode is on for "
                         f"**{RAID_MINUTES} minutes**: accounts under {RAID_ACCOUNT_DAYS} days old are kicked on join."
                         f"{raised}\nUse `/security lockdown` to also stop everyone from chatting.", ping_owner=True)
        cutoff = discord.utils.utcnow() - timedelta(seconds=RAID_SECONDS * 3)
        for member in [m for m in guild.members if m.joined_at and m.joined_at > cutoff and m.id != skip]:
            if join_block_reason(member) == "raid":
                await self.remove_new_member(member, "raid")

    async def check_bot_add(self, bot_member: discord.Member):
        guild = bot_member.guild
        if not setting(guild.id, "sec_nuke"):
            return
        actor = await self.find_actor(guild, discord.AuditLogAction.bot_add, bot_member.id)
        if actor and self.trusted(guild, actor):
            return await send_log(guild, Card(f"### 🤖 Bot Added\n{bot_member.mention} was added by {actor.mention}.",
                                              footer=False, color=ACCENT))
        try:
            await bot_member.kick(reason="Security: bot added by an untrusted member")
            result = "I kicked it."
        except discord.HTTPException:
            result = "I couldn't kick it. **Remove it manually** and move my role higher."
        self.stats[(guild.id, "nukes")] += 1
        await self.alert(guild, "🤖 Unauthorized Bot Blocked",
                         f"{bot_member.mention} (`{bot_member}`) was added by "
                         f"{actor.mention if actor else 'someone unknown'}, who isn't trusted. {result}",
                         ping_owner=True)

    # ---------- Anti-nuke ----------
    async def record_action(self, guild: discord.Guild, action: discord.AuditLogAction, target_id, what: str):
        if not setting(guild.id, "sec_nuke"):
            return
        actor = await self.find_actor(guild, action, target_id)
        if not actor or self.trusted(guild, actor):
            return
        if self.hit(self.nuke_actions[(guild.id, actor.id)], NUKE_SECONDS) >= NUKE_ACTIONS:
            self.nuke_actions[(guild.id, actor.id)].clear()
            await self.stop_nuke(guild, actor, what)

    async def stop_nuke(self, guild: discord.Guild, actor, what: str):
        member = guild.get_member(actor.id)
        self.stats[(guild.id, "nukes")] += 1
        if member is None:
            result = "They already left."
        elif member.bot:
            try:
                await member.kick(reason=f"Security: nuke attempt ({what})")
                result = "I **kicked the bot**."
            except discord.HTTPException:
                result = "I couldn't kick the bot. **Remove it now** and move my role higher."
        else:
            roles = [r for r in member.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
            try:
                await member.remove_roles(*roles, reason=f"Security: nuke attempt ({what})")
                result = f"I **removed all {len(roles)} of their roles**."
            except discord.HTTPException:
                result = "I couldn't remove their roles. **Act now**, and move my role to the top."
            if any(r >= guild.me.top_role for r in member.roles if not r.is_default()):
                result += " Some of their roles are above mine, so I couldn't take those."
        await self.alert(guild, "🚨 Nuke Attempt Stopped",
                         f"{actor.mention} (`{actor}`) was **{what}** — {NUKE_ACTIONS}+ times in under a minute. "
                         f"{result}", ping_owner=True, member=member)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.record_action(channel.guild, discord.AuditLogAction.channel_delete, channel.id, "deleting channels")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self.record_action(channel.guild, discord.AuditLogAction.channel_create, channel.id, "creating channels")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self.record_action(role.guild, discord.AuditLogAction.role_delete, role.id, "deleting roles")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user):
        await self.record_action(guild, discord.AuditLogAction.ban, user.id, "banning members")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self.record_action(member.guild, discord.AuditLogAction.kick, member.id, "kicking members")

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        await self.record_action(channel.guild, discord.AuditLogAction.webhook_create, None, "creating webhooks")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        granted = discord.Permissions(after.permissions.value & ~before.permissions.value & DANGEROUS.value)
        if not granted.value or not setting(after.guild.id, "sec_nuke"):
            return
        actor = await self.find_actor(after.guild, discord.AuditLogAction.role_update, after.id)
        if not actor or self.trusted(after.guild, actor):
            return
        try:
            await after.edit(permissions=before.permissions, reason="Security: dangerous permissions reverted")
            result = "I **reverted it**."
        except discord.HTTPException:
            result = "I couldn't revert it. **Fix it manually** and move my role higher."
        names = ", ".join(name.replace("_", " ").title() for name, value in granted if value)
        await self.alert(after.guild, "🔐 Dangerous Permissions Blocked",
                         f"{actor.mention} gave {after.mention} **{names}**. {result}", ping_owner=True)
        await self.record_action(after.guild, discord.AuditLogAction.role_update, after.id, "changing role permissions")

    # ---------- Background watch ----------
    @tasks.loop(seconds=30)
    async def watch(self):
        for guild in self.bot.guilds:
            until = get_setting(guild.id, "sec_raid_until")
            if until and time.time() > until:
                set_setting(guild.id, "sec_raid_until", None)
                previous = get_setting(guild.id, "sec_prev_verification")
                if previous is not None:
                    set_setting(guild.id, "sec_prev_verification", None)
                    try:
                        await guild.edit(verification_level=discord.VerificationLevel(previous),
                                         reason="Security: raid mode ended")
                    except discord.HTTPException:
                        pass
                await send_log(guild, Card("### ✅ Raid Mode Ended\nNew members can join normally again.",
                                           footer=False, color=ACCENT))
        # forget idle trackers so memory stays small
        for trackers in (self.message_times, self.recent_messages, self.invite_strikes, self.nuke_actions):
            for key in [k for k, q in trackers.items() if not q]:
                del trackers[key]

    @watch.before_loop
    async def before_watch(self):
        await self.bot.wait_until_ready()

    # ---------- Commands ----------
    def status_card(self, guild: discord.Guild) -> Card:
        def on(name):
            return "✅" if setting(guild.id, name) else "❌"

        days = setting(guild.id, "sec_alt_days")
        locked = db.execute("SELECT COUNT(*) FROM lockdown_channels WHERE guild_id=?", (guild.id,)).fetchone()[0]
        state = []
        if raid_active(guild.id):
            ends = datetime.fromtimestamp(get_setting(guild.id, "sec_raid_until"), tz=timezone.utc)
            state.append(f"🚨 **Raid mode on**, ends {discord.utils.format_dt(ends, 'R')}")
        if locked:
            state.append(f"🔒 **Lockdown on** ({locked} channels locked)")
        stat = lambda kind: self.stats[(guild.id, kind)]  # noqa: E731
        return Card(
            f"## 🛡️ Security Status\nOn duty since {discord.utils.format_dt(self.on_duty_since, 'R')}. No days off.",
            *([" · ".join(state)] if state else []),
            None,
            f"{on('sec_nuke')} **Anti-nuke** — {NUKE_ACTIONS} destructive actions in a minute strips roles. "
            f"Also blocks untrusted bots and dangerous permission changes.\n"
            f"{on('sec_raid')} **Anti-raid** — {RAID_JOINS} joins in {RAID_SECONDS}s turns on raid mode\n"
            f"{'✅' if days else '❌'} **Alt blocker** — "
            f"{f'kicks accounts younger than {days} day' + ('s' if days != 1 else '') if days else 'off'}\n"
            f"{on('sec_spam')} **Anti-spam** — {SPAM_MESSAGES} messages in {SPAM_SECONDS}s or "
            f"{DUPLICATES} repeats\n"
            f"{on('sec_mentions')} **Mass mentions** — {MENTION_LIMIT}+ mentions in one message\n"
            f"{on('sec_invites')} **Invite links** — other servers' invites are removed\n"
            f"{on('sec_links')} **Scam links** — fake Nitro and Steam links, 1 hour timeout",
            None,
            f"**Since I came on duty:** {stat('messages')} messages blocked · {stat('alt')} alts and "
            f"{stat('raid')} raiders removed · {stat('raids')} raids · {stat('nukes')} nuke attempts stopped",
            "-# Trusted (never punished): the server owner, the bot owner and the full-access ID. "
            "Staff skip the chat filters. Change settings with `/security config`.",
            banner="security", color=ERROR,
        )

    @security.command(name="status", description="(Staff) See what the bot is protecting against")
    @staff_only(manage_guild=True)
    async def security_status(self, interaction: discord.Interaction):
        await self.status_card(interaction.guild).respond(interaction, ephemeral=True)

    @security.command(name="config", description="(Staff) Turn protections on or off")
    @app_commands.describe(min_account_age="Kick accounts younger than this many days (0 = off)")
    @staff_only(manage_guild=True)
    async def security_config(self, interaction: discord.Interaction, anti_nuke: bool | None = None,
                              anti_raid: bool | None = None, anti_spam: bool | None = None,
                              mass_mentions: bool | None = None, invite_links: bool | None = None,
                              scam_links: bool | None = None,
                              min_account_age: app_commands.Range[int, 0, 60] | None = None):
        for name, value in {"sec_nuke": anti_nuke, "sec_raid": anti_raid, "sec_spam": anti_spam,
                            "sec_mentions": mass_mentions, "sec_invites": invite_links, "sec_links": scam_links,
                            "sec_alt_days": min_account_age}.items():
            if value is not None:
                set_setting(interaction.guild_id, name, int(value))
        await self.status_card(interaction.guild).respond(interaction, ephemeral=True)

    @security.command(name="lockdown", description="(Staff) Stop everyone from chatting in every channel")
    @staff_only(manage_guild=True)
    async def security_lockdown(self, interaction: discord.Interaction, reason: str = "Security lockdown"):
        await interaction.response.defer()
        guild, everyone = interaction.guild, interaction.guild.default_role
        locked = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(everyone)
            if overwrite.send_messages is False or not channel.permissions_for(everyone).view_channel:
                continue  # already read-only or private
            with db:
                db.execute("INSERT OR REPLACE INTO lockdown_channels VALUES (?, ?, ?)",
                           (guild.id, channel.id, None if overwrite.send_messages is None else 1))
            overwrite.send_messages = False
            try:
                await channel.set_permissions(everyone, overwrite=overwrite, reason=f"Lockdown: {reason}")
                locked += 1
            except discord.HTTPException:
                pass
        self.stats[(guild.id, "lockdowns")] += 1
        await reply(interaction, "🔒 Server Locked Down",
                    f"**{locked} channels** are now read-only for everyone.\n**Reason:** {reason}\n"
                    "-# Staff can still talk. Use `/security unlock` when it's safe.", color=ERROR)
        await self.alert(guild, "🔒 Lockdown Started",
                         f"{interaction.user.mention} locked **{locked} channels**.\n**Reason:** {reason}")

    @security.command(name="unlock", description="(Staff) End a lockdown and restore every channel")
    @staff_only(manage_guild=True)
    async def security_unlock(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild, everyone = interaction.guild, interaction.guild.default_role
        rows = db.execute("SELECT channel_id, previous FROM lockdown_channels WHERE guild_id=?", (guild.id,)).fetchall()
        restored = 0
        for channel_id, previous in rows:
            channel = guild.get_channel(channel_id)
            if channel:
                overwrite = channel.overwrites_for(everyone)
                overwrite.send_messages = None if previous is None else True
                try:
                    await channel.set_permissions(everyone, overwrite=overwrite, reason="Lockdown ended")
                    restored += 1
                except discord.HTTPException:
                    pass
        with db:
            db.execute("DELETE FROM lockdown_channels WHERE guild_id=?", (guild.id,))
        await reply(interaction, "🔓 Lockdown Ended", f"**{restored} channels** are back to normal.", color=ACCENT)
        await self.alert(guild, "🔓 Lockdown Ended", f"{interaction.user.mention} unlocked **{restored} channels**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Security(bot))
