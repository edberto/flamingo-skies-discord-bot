# flamingo-skies-discord-bot

Discord bot for the Flamingo Skies Pier server. Runs on the same GCP e2-micro as the Icarus clan bot, as a separate systemd service.

## What it does
- **Builds the server layout** on every start, creating only what's missing (it never edits or deletes):

  | Category | Who sees it | Channels |
  |---|---|---|
  | 🌅 FLAMINGO SKIES PIER | Everyone | `#announcements` (studio-wide; Announcement channel, needs Community), `#playing-now` (read-only), `#bot-commands` (for fun bots) |
  | 🔒 ADMIN | Admins only | `#studio-stats`, `#vesper-log` |
  | One per game, e.g. HUSS GAME | That game's role, `<game>-expert`, Moderator, Dev | `#announcements` (that game's news), `#codes` (staff post, players read; Announcement channel), `#media`, `#chat`, `#questions`, `#suggestions` (forum), `#bug-report` (forum), `#top-suggestions` (Moderator, Dev and admins only) |

  Channel names are decorated (`💬・chat`, `🎮 HUSS GAME`); Vesper matches names with the emoji ignored, so plain-named channels from older versions are renamed once and put in order, and channels you rename yourself are left alone.
  In every `#announcements`, only admins, Moderator, Dev and Vesper can post. To add a game later, add it to `GAMES` and restart.

  Roles: **Moderator** and **Dev** (moderation powers but not Administrator, so they can't see ADMIN), **Tester**, **Flamingo**, and per game a role named after the game (handed out by Onboarding) plus `<game>-expert`. Tester, Flamingo and the game roles have member-level permissions.
- **Join-to-create voice rooms** (same as the clan bot): FLAMINGO SKIES PIER and every game category get a "Join me to create" voice channel. Joining it makes you your own room in that category (you can rename it or set a user limit); it's deleted when the last person leaves. Game rooms stay visible only to that game's players.
- **`/post-rules`, `/post-welcome`** (Manage Server): post `rules.md` in #rules and `welcome.md` (pinned) in #general. Edit the file, push, run the command again: Vesper deletes its previous copy first. `{#announce}`, `{#status}`, `{#botcmds}`, `{#rules}` become clickable channel mentions.
- **`#vesper-log`**: "Started, version `abc1234 ...`" on every start, channels it created, auto-update problems (skipped or rolled-back commits) and digest failures. Same idea as the clan bot's `#bot-commands`.
- **Nightly backup** at 03:00 Asia/Jakarta: `fs.db` → `/opt/fsbot/backups/fs-Mon.db` … `fs-Sun.db` (7 days).
- **Live "Playing now" card** in `#playing-now`, edited every 5 minutes (one message, never spams).
- **Visit milestones** (1K, 2.5K, 5K, 10K, 25K, ...) posted to that game's `#announcements` and published to following servers. On first run it only remembers the current milestone, so it never announces old ones.
- **Daily digest** at `DIGEST_HOUR` (Asia/Jakarta) in `#studio-stats`: DAU, visits, session length, D1/D7 retention, revenue, paying users, crashes. It uses Roblox's Open Cloud Analytics API, which is still Beta.
- `/stats game` for everyone. `/announce [game]` (no game: studio-wide `#announcements`; with a game: that game's `#announcements`, with a Play button) and `/digest-now` for people with **Manage Server**.

A game with universe ID `0` is a placeholder: it shows "Not published yet" and the loops skip it. When the game is published, put its universe ID in `GAMES` and restart.

## 1. Discord app (5 min)
1. https://discord.com/developers/applications → **New Application** "Flamingo Skies". Under **Bot**, copy the token into `.env`. Leave every Privileged Gateway Intent **off**.
2. Invite it with exactly the permissions it needs to build the layout (it can only hand Moderator/Dev permissions it holds itself). Replace `APP_ID` with **General Information → Application ID**:
   `https://discord.com/oauth2/authorize?client_id=APP_ID&permissions=1436026137782&scope=bot+applications.commands`
3. Turn on Community first (Server Settings → Enable Community), so `#announcements` is created as an Announcement channel.
4. After the first start: Server Settings → Roles → drag **Vesper** to the top, above Moderator and Dev.
5. **Onboarding** (Server Settings → Onboarding): add a question "Which games do you play?" with one answer per game, each giving that game's role (e.g. "Huss game"). Add a required question that gives everyone **Flamingo**. Members can change their games later in Channels & Roles.

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
Optional: `sudo sh /opt/fsbot/app/deploy/setup.sh auto` turns on the same tested, rolled-back GitHub auto-deploy the clan bot uses. Skipped or rolled-back updates show up in `#vesper-log` (full log: `journalctl -u fsbot-update -n 50`). Manual update: `sudo sh /opt/fsbot/app/deploy/update.sh`.

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
