# Hawaii Studio Bot

Runs 24/7 on Railway. Every push to the `main` branch on GitHub redeploys it automatically.

## Files
| File | What it does |
|---|---|
| `bot.py` | Starts the bot and loads everything |
| `core.py` | Brand colors, database, shared helpers, the branded `Card` layout |
| `cogs/general.py` | `/help`, fun commands, `/config`, welcome messages, auto role |
| `cogs/panels.py` | `/panel rules`, `/panel tickets`, `/panel stocks`, `/panel boosters` and `/panel verify` |
| `cogs/tickets.py` | Ticket types and their questions (`TICKET_TYPES`), transcripts |
| `cogs/moderation.py` | Kick, ban, timeout, purge + mod log |
| `cogs/security.py` | Anti-nuke, bot and permission guards, anti-raid, alt blocker, spam/invite/scam filters, lockdown |
| `cogs/economy.py` | Coins, `/daily`, `/pay`, `/leaderboard` |
| `cogs/stocks.py` | Stock market panel, `/stock add/edit/remove/portfolio`, Buy tickets and sales |
| `cogs/announcements.py` | `/announce`, `/announceall`, `/schedule` |
| `rules.json` | The rules shown in the rules panel and its dropdown |
| `assets/` | Banner images. Regenerate with `python tools/make_banners.py` |
| `.env` | Your bot token for running locally. **Never share or upload this** |

## First-time server setup
1. `/config logs #staff-logs` (mod actions, ticket transcripts and stock sales go here)
2. `/config welcome channel:#welcome verify_channel:#verify`. Welcome messages tell new members to verify there first, and both channels are made visible to unverified members.
3. Optional: `/panel verify role:@Member` in your verify channel if you want this bot's own Verify button to hand out a role.
4. `/panel rules` in your rules channel
5. `/panel tickets category:Tickets support_role:@Staff` in your support channel
6. `/panel boosters` in your boosts/perks channel
7. `/panel stocks` in your market channel, then `/stock add` for each server you sell pieces of

If you use the verify button, don't also give `@Member` with `/config autorole`, or new members skip verification.

## Changing the rules
Edit `rules.json`, push to GitHub, then run `/panel rules` again and delete the old panel.
The first section is shown on the panel itself; every section appears in the dropdown.

## Security
`/security status` shows what's on; `/security config` turns protections on or off and sets the minimum account age (default 3 days). `/security lockdown` and `/security unlock` lock and restore every channel.
Keep the bot's role at the **top** of the role list so it can stop anyone below it.

## Full access
The bot owner (`OWNER_ID`) and the IDs in `MANAGERS` (a user or role ID, default `1473005622231564318`) can use every command, including `/announce` and `/schedule`. Change it in Railway's Variables tab.

## Railway settings
Variables: `DISCORD_TOKEN`, `OWNER_ID`, `DB_PATH=/data/bot.db`, with a volume mounted at `/data`.
Only one copy of the bot should run at a time, so don't run it on your PC while Railway is running it.
