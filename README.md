# flamingo-skies-discord-bot

Discord bot for the Flamingo Skies server. Runs on the same GCP e2-micro as the Icarus clan bot, as a separate systemd service.

## What it does
- **Live "Playing now" card** in `STATUS_CHANNEL_ID`, edited every 5 minutes (one message, never spams).
- **Visit milestones** (1K, 2.5K, 5K, 10K, 25K, ...) posted to `ANNOUNCE_CHANNEL_ID` and published to following servers. On first run it only remembers the current milestone, so it never announces old ones.
- **Daily digest** at `DIGEST_HOUR` (Asia/Jakarta) in `STATS_CHANNEL_ID`: DAU, visits, session length, D1/D7 retention, revenue, paying users, crashes. It uses Roblox's Open Cloud Analytics API, which is still Beta.
- `/stats game` for everyone. `/announce [game]` and `/digest-now` for people with **Manage Server**.

A game with universe ID `0` is a placeholder: it shows "Not published yet" and the loops skip it. When the game is published, put its universe ID in `GAMES` and restart.

## 1. Discord app (5 min)
1. https://discord.com/developers/applications → **New Application** "Flamingo Skies". Under **Bot**, copy the token into `.env`. Leave every Privileged Gateway Intent **off**.
2. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`. Permissions: View Channels, Send Messages, Embed Links, Read Message History, Send Messages in Threads. Open the URL and add the bot to the server.
3. Make the channels: `#playing-now` (read-only for members), `#announcements` (Announcement channel), `#studio-stats` (private: only you and the bot). Copy their IDs into `.env`.

## 2. Roblox key (3 min)
Creator Dashboard → **Credentials → API Keys → Create API Key**. Add **universe-analytics** with only `universe.analytics:read`, on your games only. Paste it as `ROBLOX_ANALYTICS_KEY`. You can leave it empty until a game is published.

## 3. Server: next to the clan bot
SSH into the VM (same one as `clanbot`). Upload `deploy/setup.sh`, then:
```bash
sudo sh ~/setup.sh install     # fsbot user, venv, 1 GB swap, memory caps on BOTH bots, fsbot.service
sudo sh ~/setup.sh key         # add the printed key at github.com/edberto/flamingo-skies-discord-bot/settings/keys/new (read-only)
sudo nano /opt/fsbot/.env      # paste your filled-in .env
sudo sh ~/setup.sh clone       # clone, install, start
journalctl -u fsbot -f
```
Optional: `sudo sh /opt/fsbot/app/deploy/setup.sh auto` turns on the same tested, rolled-back GitHub auto-deploy the clan bot uses (`journalctl -u fsbot-update -n 50`). Manual update: `sudo sh /opt/fsbot/app/deploy/update.sh`.

How the two bots share the machine:

| | clanbot | fsbot |
|---|---|---|
| Linux user | `clanbot` | `fsbot` |
| Folder | `/opt/clanbot` | `/opt/fsbot` |
| Secrets | `/opt/clanbot/.env` | `/opt/fsbot/.env` |
| Service | `clanbot.service` | `fsbot.service` |
| Memory cap | 300 MB soft / 400 MB hard | 300 MB soft / 400 MB hard |

Each bot runs as its own user and can only write its own folder, so neither can read the other's tokens. The 1 GB swap file absorbs short spikes. Check with `free -m` and `systemctl status clanbot fsbot`.

**Why not Docker:** on a 1 GB e2-micro the Docker daemon takes RAM the bots need. The clan bot's tested auto-deploy is also built on systemd. Separate users, venvs and services already keep the bots apart. Revisit if you move to a bigger machine or add a third service with system dependencies.

Run the checks: `python3 test_bot.py`
