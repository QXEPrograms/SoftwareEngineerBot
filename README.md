# Hawaii Studio Bot

Runs 24/7 on Railway. Every push to the `main` branch on GitHub redeploys it automatically.

## Files
| File | What it does |
|---|---|
| `bot.py` | Starts the bot and loads everything |
| `core.py` | Brand colors, database, shared helpers, the branded `Card` layout |
| `cogs/general.py` | `/help`, fun commands, `/config`, welcome messages, auto role |
| `cogs/panels.py` | `/panel rules` and `/panel tickets` |
| `cogs/tickets.py` | Ticket buttons, transcripts |
| `cogs/moderation.py` | Kick, ban, timeout, purge + mod log |
| `cogs/economy.py` | Coins, `/pay`, `/leaderboard`, server stocks, dividends |
| `cogs/announcements.py` | `/announce`, `/announceall`, `/schedule` |
| `rules.json` | The rules shown in the rules panel and its dropdown |
| `assets/` | Banner images. Regenerate with `python tools/make_banners.py` |
| `.env` | Your bot token for running locally. **Never share or upload this** |

## First-time server setup
1. `/config logs #staff-logs` (mod actions, ticket transcripts and dividend payouts go here)
2. `/config welcome #welcome`
3. `/config autorole @Member`
4. `/panel rules` in your rules channel
5. `/panel tickets category:Tickets support_role:@Staff` in your support channel
6. `/stock setup total_shares:1000 price:50 dividend:1`

## Changing the rules
Edit `rules.json`, push to GitHub, then run `/panel rules` again and delete the old panel.
The first section is shown on the panel itself; every section appears in the dropdown.

## Railway settings
Variables: `DISCORD_TOKEN`, `OWNER_ID`, `DB_PATH=/data/bot.db`, with a volume mounted at `/data`.
Only one copy of the bot should run at a time, so don't run it on your PC while Railway is running it.
