# Hawaii Studio Bot

## Running it on your PC
Double-click **start.bat**. It restarts the bot automatically if it crashes. Close the window to stop it.

## Files
| File | What it does |
|---|---|
| `bot.py` | Starts the bot and loads everything |
| `core.py` | Brand colors, database, shared helpers |
| `cogs/general.py` | `/help`, fun commands, `/config`, welcome messages, auto role |
| `cogs/tickets.py` | Ticket panel, transcripts |
| `cogs/moderation.py` | Kick, ban, timeout, purge + mod log |
| `cogs/economy.py` | Coins, `/pay`, `/leaderboard`, server stocks, dividends |
| `cogs/announcements.py` | `/announce`, `/announceall`, `/schedule` |
| `.env` | Your bot token — **never share or upload this** |
| `bot.db` | All saved data (coins, shares, settings) |

## First-time server setup
1. `/config logs #staff-logs` — mod actions, ticket transcripts and dividend payouts go here
2. `/config welcome #welcome`
3. `/config autorole @Member`
4. `/ticketpanel category:Tickets support_role:@Staff` in your support channel
5. `/stock setup total_shares:1000 price:50 dividend:1`

## Hosting 24/7 on Railway
1. Put this folder in a **private** GitHub repo. `.gitignore` already keeps `.env` and `bot.db` out of it.
2. On railway.com: **New Project → Deploy from GitHub repo** and pick it.
3. In the service's **Variables** tab, add `DISCORD_TOKEN` and `OWNER_ID`.
4. So the database survives redeploys: **Add Volume**, mount it at `/data`, and add the variable `DB_PATH=/data/bot.db`.
5. Stop the bot on your PC first. Only one copy should run at a time.
