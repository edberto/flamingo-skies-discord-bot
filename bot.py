"""Vesper, the Flamingo Skies studio bot.

- On start: creates any missing roles, categories and channels of the server layout (layout() below). Each game
  in GAMES gets a role named after it and a category only that role (plus its experts and staff) can see; Discord
  Onboarding hands out the game roles. Finds existing things by name and never edits or deletes anything.
- Join-to-create voice (same as the clan bot): joining "Join me to create" in FLAMINGO SKIES or a game category
  makes you your own room there (you can rename/limit it); it's deleted when the last person leaves.
- #vesper-log: "Started, version ..." on every start, channels it created, auto-update problems, digest failures.
- Nightly at 03:00 Asia/Jakarta: copies fs.db to backups/fs-<weekday>.db (keeps 7 days).
- Every 5 min: public Roblox stats for each game -> one live "playing now" card in #playing-now,
  and a post in that game's #announcements when a game passes a visit milestone (1K, 2.5K, 5K, 10K, ...).
- Daily at DIGEST_HOUR Asia/Jakarta: private analytics digest (Open Cloud Analytics, Beta) in #studio-stats.
- /stats: a game's card. /announce (Manage Server): studio-wide post, or with a game: in that game's
  #announcements with a Play button. /digest-now, /post-rules, /post-welcome (Manage Server):
  the last two post rules.md / welcome.md and replace their previous copy.
A game whose universe ID is 0 is a placeholder: it shows as "not published yet" and is skipped by the loops.
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

log = logging.getLogger("fsbot")

if Path(".env").exists():  # local runs; systemd sets these itself on the server
    for _line in Path(".env").read_text().splitlines():
        _k, _, _v = _line.partition("=")
        if _v and not _k.strip().startswith("#"):
            os.environ.setdefault(_k.strip(), _v.strip())

JKT = ZoneInfo("Asia/Jakarta")
PINK = 0xF25C9A
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
# Channels the bot itself posts in: key -> optional ID override from .env (else found by name / created)
OVERRIDES = {"status": int(os.environ.get("STATUS_CHANNEL_ID") or 0),
             "announce": int(os.environ.get("ANNOUNCE_CHANNEL_ID") or 0),
             "stats": int(os.environ.get("STATS_CHANNEL_ID") or 0), "log": 0, "botcmds": 0}
APP_DIR = Path(__file__).resolve().parent
# /post-rules and /post-welcome: file in the repo -> channel (Discord's own #rules / #general), pinned?
POSTS = {"rules": ("rules.md", "rules", False), "welcome": ("welcome.md", "general", True)}
ANALYTICS_KEY = os.environ.get("ROBLOX_ANALYTICS_KEY", "")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "9"))
DB_PATH = Path(os.environ.get("DB_PATH", "fs.db"))
DEPLOY_NOTE = DB_PATH.resolve().parent / "deploy-note.txt"  # written by deploy/auto-update.sh

# (Open Cloud metric, label, kind). Kinds: n = count, pct = 0..1 ratio, min = minutes, rbx = Robux.
DIGEST_METRICS = [
    ("DailyActiveUsers", "Daily active users", "n"),
    ("Visits", "Visits", "n"),
    ("AverageSessionLengthMinutes", "Avg session", "min"),
    ("ForwardD1Retention", "D1 retention", "pct"),
    ("ForwardD7Retention", "D7 retention", "pct"),
    ("DailyRevenue", "Revenue", "rbx"),
    ("PayingUsers", "Paying users", "n"),
    ("ClientCrashCount", "Client crashes", "n"),
]


def parse_games(raw):
    """'Huss game:0, Other:123' -> {'Huss game': 0, 'Other': 123}"""
    games = {}
    for part in raw.split(","):
        name, _, uid = part.rpartition(":")
        if name.strip():
            games[name.strip()] = int(uid.strip() or 0)
    return games


GAMES = parse_games(os.environ.get("GAMES", "Huss game:0"))
HUB = "🔊 Join me to create"


def plain(name):
    """'💬・chat' -> 'chat', '🎮 HUSS GAME' -> 'HUSS GAME': the name without its emoji decoration."""
    return re.sub(r"^[^\w]+", "", name.split("・", 1)[-1]).strip()


def same(a, b):
    """Names match when they're equal once decoration and case are ignored."""
    return plain(a).lower() == plain(b).lower()


def find(items, name):
    return next((c for c in items if same(c.name, name)), None)

# "Admin privileges except the admin category": everything a moderator needs, but not Administrator
# (Administrator would bypass the ADMIN category's lock). The bot must hold these itself to hand them out.
STAFF_PERMS = discord.Permissions(
    manage_guild=True, manage_roles=True, manage_channels=True, kick_members=True, ban_members=True,
    moderate_members=True, manage_messages=True, manage_threads=True, manage_nicknames=True, view_audit_log=True,
    mute_members=True, deafen_members=True, move_members=True, manage_events=True, manage_expressions=True,
    mention_everyone=True)
BOT_PERMS = STAFF_PERMS | discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    send_messages_in_threads=True, create_public_threads=True, connect=True)  # connect: to move people into rooms


def slug(name):
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def layout(games):
    """[(category, who sees it, [(channel, kind, key)])]. Who: None = everyone, "admin" = admins only,
    a game name = that game's role, its experts, Moderator and Dev.
    Kinds: text, readonly, news, forum, voice, hidden (admins, Moderator and Dev only)."""
    cats = [("🌅 FLAMINGO SKIES PIER", None, [("📢・announcements", "news", "announce"),
                                          ("🎮・playing-now", "readonly", "status"),
                                          ("🤖・bot-commands", "text", "botcmds"), (HUB, "voice", None)]),
            ("🔒 ADMIN", "admin", [("📊・studio-stats", "text", "stats"), ("🌟・vesper-log", "text", "log")])]
    for game in games:
        cats.append((f"🎮 {game.upper()}", game, [
            ("📢・announcements", "news", f"announce:{game}"), ("🎟️・codes", "news", None),  # staff post codes
            ("🖼️・media", "text", None), ("💬・chat", "text", None),
            ("❓・questions", "text", None), ("💡・suggestions", "forum", None), ("🐛・bug-report", "forum", None),
            ("🏆・top-suggestions", "hidden", None),  # staff only
            (HUB, "voice", None)]))
    return cats


def roles(games):
    """[(name, permissions, colour, hoist)]. Member-like roles get no extra permissions (they inherit @everyone)."""
    return ([("Moderator", STAFF_PERMS, 0xFF7A59, True), ("Dev", STAFF_PERMS, 0x9D8CC4, True),
             ("Tester", discord.Permissions.none(), 0xFFE3A3, False),
             ("Flamingo", discord.Permissions.none(), 0xF48FB4, False)]
            + [r for g in games for r in ((g, discord.Permissions.none(), 0, False),  # game access, from Onboarding
                                          (f"{slug(g)}-expert", discord.Permissions.none(), 0xFFC1D4, False))])


def milestones_passed(visits):
    """Highest milestone at or below visits: 1K, 2.5K, 5K, 10K, 25K, 50K, ... (0 if under 1K)."""
    best, scale = 0, 1000
    while scale <= visits:
        for m in (scale, scale * 5 // 2, scale * 5):
            if m <= visits:
                best = m
        scale *= 10
    return best


def short(n):
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            return f"{n / div:.2f}".rstrip("0").rstrip(".") + suffix
    return str(n)


def fmt(value, kind):
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.1f}%" if value <= 1 else f"{value:.1f}%"  # ponytail: doc doesn't say ratio vs percent
    if kind == "min":
        return f"{value:.1f} min"
    if kind == "rbx":
        return f"R$ {value:,.0f}"
    return f"{value:,.0f}"


def latest_point(body):
    """Last data point with a value from a completed Analytics response -> (day, value) or (None, None)."""
    for series in body.get("response", {}).get("values", [])[:1]:
        points = [p for p in series.get("dataPoints", []) if p.get("value") is not None]
        if points:
            return points[-1]["time"][:10], points[-1]["value"]
    return None, None


def stats_card(name, game, icon=None):
    """Components V2 card for one game. game = Games API row, or None for a placeholder."""
    if not game:
        body = f"## {name}\nNot published yet."
        return discord.ui.LayoutView().add_item(discord.ui.Container(discord.ui.TextDisplay(body), accent_colour=PINK))
    body = (f"## {game['name']}\n**{game['playing']:,}** playing now\n"
            f"{game['visits']:,} visits · {game.get('favoritedCount', 0):,} favorites")
    top = discord.ui.Section(body, accessory=discord.ui.Thumbnail(icon)) if icon else discord.ui.TextDisplay(body)
    play = discord.ui.Button(label="Play", url=f"https://www.roblox.com/games/{game['rootPlaceId']}")
    return discord.ui.LayoutView().add_item(discord.ui.Container(top, discord.ui.ActionRow(play), accent_colour=PINK))


def status_card(rows):
    """One card listing every game, for the live message. rows = [(name, game or None)]."""
    lines = [f"**{name}**: " + (f"{g['playing']:,} playing · {short(g['visits'])} visits" if g else "not published yet")
             for name, g in rows]
    stamp = f"-# Updated <t:{int(datetime.now(timezone.utc).timestamp())}:R>"
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("## Playing now\n" + "\n".join(lines) + "\n" + stamp),
                                       accent_colour=PINK))
    return view


def announce_card(title, message, game=None):
    items = [discord.ui.TextDisplay(f"## {title}\n{message}")]
    if game:
        items.append(discord.ui.ActionRow(discord.ui.Button(
            label=f"Play {game['name']}", url=f"https://www.roblox.com/games/{game['rootPlaceId']}")))
    return discord.ui.LayoutView().add_item(discord.ui.Container(*items, accent_colour=PINK))


def digest_text(name, results):
    """results = {metric: (day, value)} -> markdown block for one game."""
    lines = [f"### {name}"]
    for metric, label, kind in DIGEST_METRICS:
        day, value = results.get(metric, (None, None))
        lines.append(f"{label}: **{fmt(value, kind)}**" + (f" ({day})" if day else ""))
    return "\n".join(lines)


def running_version():
    """'abc1234 commit message' when running from a git checkout, else None."""
    try:
        out = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent), "log", "-1", "--format=%h %s"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def fill_mentions(text, ids):
    """'{#announce}' -> '<#123>' for every known channel key; unknown keys stay as plain text."""
    for key, cid in ids.items():
        text = text.replace("{#" + key + "}", f"<#{cid}>" if cid else f"#{key}")
    return text


def split_message(text, limit=1900):
    """Split at blank lines so each piece fits in one Discord message."""
    parts, cur = [], ""
    for block in text.strip().split("\n\n"):
        if cur and len(cur) + 2 + len(block) > limit:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    return parts + [cur] if cur else parts


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS rooms (channel_id INTEGER PRIMARY KEY, owner_id INTEGER)")
    return db


class FSBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())  # no privileged intents
        self.tree = app_commands.CommandTree(self)
        self.db = open_db()
        self.http_session = None
        self.last_room = {}  # member id -> when we last made them a room

    def get(self, k):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, str(v)))
        self.db.commit()

    def channel(self, key):
        return self.get_channel(OVERRIDES.get(key) or int(self.get(f"channel:{key}") or 0))

    def announce_channel(self, game_name=None):
        """The game's #announcements if it has one, else the studio-wide #announcements."""
        return (game_name and self.channel(f"announce:{game_name}")) or self.channel("announce")

    async def ensure_layout(self, guild):
        """Create missing roles, categories and channels; returns notes about what it made.
        Existing ones are found by name (bot channels also by saved/.env ID) and left exactly as they are."""
        notes, everyone = [], guild.default_role
        bot_can = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                              send_messages_in_threads=True, connect=True)
        hide, see = discord.PermissionOverwrite(view_channel=False), discord.PermissionOverwrite(view_channel=True)
        role = {}
        for name, perms, colour, hoist in roles(GAMES):
            role[name] = discord.utils.get(guild.roles, name=name)
            if not role[name]:
                role[name] = await guild.create_role(name=name, permissions=perms, colour=colour, hoist=hoist,
                                                     reason="Vesper setup")
                notes.append(f"Created role {name}")
        for cat_name, who, channels in layout(GAMES):
            cat = find(guild.categories, cat_name)
            if cat and cat.name == plain(cat_name):  # still the old plain name: decorate it once
                await cat.edit(name=cat_name, reason="Vesper: prettier names")
                notes.append(f"Renamed {plain(cat_name)} to {cat_name}")
            if not cat:
                ow = {}
                if who:  # locked category: hidden from everyone, then opened to the right roles
                    ow = {everyone: hide, guild.me: bot_can}
                if who and who != "admin":
                    for r in (who, f"{slug(who)}-expert", "Moderator", "Dev"):
                        ow[role[r]] = see
                cat = await guild.create_category(cat_name, overwrites=ow, reason="Vesper setup")
                notes.append(f"Created category {cat_name}")
            renamed, ordered = False, []
            for name, kind, key in channels:
                ch = guild.get_channel(OVERRIDES.get(key) or int(self.get(f"channel:{key}") or 0)) if key else None
                ch = ch or find(cat.channels, name)  # names repeat across game categories
                if ch and ch.name == plain(name) and ch.name != name:  # old plain name: decorate it once
                    await ch.edit(name=name, reason="Vesper: prettier names")
                    notes.append(f"Renamed {plain(name)} to {name} in {cat_name}")
                    renamed = True
                if not ch:
                    ow = dict(cat.overwrites)  # start from the category's lock, then add the channel's own
                    if kind in ("readonly", "news"):  # add "no posting" on top of the category's view lock
                        ow[everyone] = discord.PermissionOverwrite.from_pair(*ow.get(everyone, discord.PermissionOverwrite()).pair())
                        ow[everyone].update(send_messages=False)
                        ow[guild.me] = bot_can
                    if kind == "news":  # staff post announcements too
                        for r in ("Moderator", "Dev"):
                            ow[role[r]] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
                    if kind == "hidden":  # staff only, even inside a game category
                        ow = {everyone: hide, guild.me: bot_can, role["Moderator"]: see, role["Dev"]: see}
                    if kind == "forum":
                        ch = await guild.create_forum(name, category=cat, overwrites=ow, reason="Vesper setup")
                    elif kind == "voice":
                        ch = await guild.create_voice_channel(name, category=cat, overwrites=ow, reason="Vesper setup")
                    else:
                        news = kind == "news" and "COMMUNITY" in guild.features
                        ch = await guild.create_text_channel(name, category=cat, news=news, overwrites=ow,
                                                             reason="Vesper setup")
                        if kind == "news" and not news:
                            notes.append(f"#{name} is a normal channel: turn on Community, then make it an Announcement channel")
                    notes.append(f"Created {'' if kind == 'voice' else '#'}{name} in {cat_name}")
                    renamed = True  # re-sort below so new channels land in layout order
                if key:
                    self.put(f"channel:{key}", ch.id)
                ordered.append(ch)
            if renamed:  # after a rename or an addition: put the category's channels in layout order
                for i, ch in enumerate(ordered):
                    if ch.category_id == cat.id:
                        await ch.edit(position=i, reason="Vesper: channel order")
        for note in notes:
            log.info(note)
        return notes

    async def post_doc(self, guild, name):
        """Post rules.md / welcome.md into its channel, replacing Vesper's previous copy."""
        file, channel_name, pin = POSTS[name]
        ch = (guild.rules_channel if name == "rules" else None) or find(guild.text_channels, channel_name)
        if not ch:
            return f"No #{channel_name} channel found."
        rules = guild.rules_channel or find(guild.text_channels, "rules")
        ids = {k: (self.channel(k).id if self.channel(k) else 0) for k in ("announce", "status", "botcmds")}
        ids["rules"] = rules.id if rules else 0
        text = fill_mentions((APP_DIR / file).read_text(encoding="utf-8"), ids)
        if not ch.permissions_for(guild.me).send_messages:  # e.g. Discord's read-only #rules: let Vesper post there
            await ch.set_permissions(guild.me, view_channel=True, send_messages=True, read_message_history=True,
                                     reason="Vesper: post rules/welcome")
        for mid in json.loads(self.get(f"post:{name}") or "[]"):  # remove the old copy
            try:
                await (await ch.fetch_message(mid)).delete()
            except discord.HTTPException:
                pass
        msgs = [await ch.send(part, suppress_embeds=True, allowed_mentions=discord.AllowedMentions.none())
                for part in split_message(text)]
        self.put(f"post:{name}", json.dumps([m.id for m in msgs]))
        note = ""
        if pin:
            try:
                await msgs[0].pin()
            except discord.HTTPException:
                note = " (couldn't pin it: give Vesper Pin Messages, or pin it yourself)"
        return f"Posted in {ch.mention}{note}: {msgs[0].jump_url}"

    async def bot_log(self, text):
        """Post to #vesper-log; falls back to the server log if the channel is missing."""
        log.info("bot-log: %s", text)
        channel = self.channel("log")
        try:
            if channel:
                await channel.send(text[:2000])
        except discord.HTTPException:
            log.warning("couldn't post to #vesper-log")

    async def post_deploy_note(self):
        """Relay problems from the auto-updater (skipped or rolled-back updates) to #vesper-log."""
        try:
            if DEPLOY_NOTE.exists():
                text = DEPLOY_NOTE.read_text(encoding="utf-8").strip()
                DEPLOY_NOTE.unlink()
                if text:
                    await self.bot_log("**Auto-update**\n" + text)
        except OSError:
            log.exception("couldn't read the deploy note")

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        register_commands(self)
        guild = discord.Object(GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.stats_loop.start()
        self.digest_loop.start()
        self.backup_loop.start()

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        await super().close()

    # --- Roblox ---------------------------------------------------------------------------
    async def fetch_games(self):
        """{universe_id: Games API row} for every configured, published game (public API, no key)."""
        ids = [u for u in GAMES.values() if u]
        if not ids:
            return {}
        url = "https://games.roblox.com/v1/games?universeIds=" + ",".join(map(str, ids))
        async with self.http_session.get(url) as r:
            r.raise_for_status()
            return {g["id"]: g for g in (await r.json())["data"]}

    async def fetch_icon(self, universe_id):
        url = f"https://thumbnails.roblox.com/v1/games/icons?universeIds={universe_id}&size=512x512&format=Png"
        try:
            async with self.http_session.get(url) as r:
                data = (await r.json())["data"]
                return data[0]["imageUrl"] if data and data[0].get("state") == "Completed" else None
        except Exception:
            return None

    async def query_metric(self, universe_id, metric, start, end):
        headers = {"x-api-key": ANALYTICS_KEY}
        base = "https://apis.roblox.com/analytics-query-api/"
        body = {"metric": metric, "granularity": "OneDay",
                "startTime": f"{start}T00:00:00Z", "endTime": f"{end}T00:00:00Z"}
        async with self.http_session.post(f"{base}v1/universes/{universe_id}/metrics", json=body, headers=headers) as r:
            data = await r.json()
        for _ in range(30):  # slow queries come back done=false with a path to poll
            if data.get("done") or "error" in data:
                break
            await asyncio.sleep(2)
            async with self.http_session.get(base + data["path"], headers=headers) as r:
                data = await r.json()
        if "error" in data:
            log.warning("analytics %s for %s: %s", metric, universe_id, data["error"])
        return latest_point(data)

    # --- loops ----------------------------------------------------------------------------
    @tasks.loop(minutes=5)
    async def stats_loop(self):
        await self.post_deploy_note()
        try:
            games = await self.fetch_games()
        except Exception:
            log.exception("Games API failed")
            return
        await self.update_status(games)
        await self.check_milestones(games)

    @stats_loop.before_loop
    async def _wait_stats(self):
        await self.wait_until_ready()
        guild = self.get_guild(GUILD_ID)
        if not guild:
            log.error("Not in server %s: invite the bot, check GUILD_ID", GUILD_ID)
            return
        try:
            notes = await self.ensure_layout(guild)
        except discord.Forbidden:
            notes = ["Couldn't finish the server layout: Vesper is missing a permission (see README, invite link), "
                     "or its role isn't at the top of the role list"]
            log.error(notes[0])
        for (cid,) in self.db.execute("SELECT channel_id FROM rooms").fetchall():  # rooms emptied while we were down
            ch = self.get_channel(cid)
            if ch is None or not ch.members:
                await self.drop_room(ch, cid)
        version = running_version()
        await self.bot_log("\n".join(["**Started**" + (f", version `{version}`" if version else "")]
                                     + [f"- {n}" for n in notes]))

    # --- join-to-create voice rooms (ported from the clan bot) ------------------------------
    def room_ids(self):
        return {r[0] for r in self.db.execute("SELECT channel_id FROM rooms")}

    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel or member.guild.id != GUILD_ID:
            return
        if before.channel and before.channel.id in self.room_ids() and not before.channel.members:
            await self.drop_room(before.channel, before.channel.id)
        hub = after.channel
        if (hub and same(hub.name, HUB) and hub.category
                and any(same(hub.category.name, c) for c, _, _ in layout(GAMES))):
            # hub-hopping would make the bot create/delete channels nonstop and hit Discord's rate limits
            if time.monotonic() - self.last_room.get(member.id, -60) < 15:
                return await member.move_to(None)
            self.last_room[member.id] = time.monotonic()
            ow = dict(hub.category.overwrites)
            ow[member] = discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True)
            room = await hub.category.create_voice_channel(f"{member.display_name}'s room"[:100], overwrites=ow)
            self.db.execute("INSERT INTO rooms VALUES (?, ?)", (room.id, member.id))
            self.db.commit()
            try:
                await member.move_to(room)
            except discord.HTTPException:  # they left the hub before we could move them
                await self.drop_room(room, room.id)

    async def drop_room(self, ch, cid):
        self.db.execute("DELETE FROM rooms WHERE channel_id=?", (cid,))
        self.db.commit()
        if ch is not None:
            try:
                await ch.delete(reason="empty join-to-create room")
            except discord.NotFound:
                pass

    async def update_status(self, games):
        channel = self.channel("status")
        if not channel:
            return
        view = status_card([(name, games.get(uid)) for name, uid in GAMES.items()])
        msg_id = self.get("status_message")
        try:
            if msg_id:
                await (await channel.fetch_message(int(msg_id))).edit(view=view)
                return
        except discord.NotFound:
            pass  # someone deleted it; post a new one
        self.put("status_message", (await channel.send(view=view)).id)

    async def check_milestones(self, games):
        names = {uid: name for name, uid in GAMES.items()}
        for uid, game in games.items():
            channel = self.announce_channel(names.get(uid))
            reached, key = milestones_passed(game["visits"]), f"milestone:{uid}"
            last = self.get(key)
            if last is None:  # first sight of this game: remember, don't announce the backlog
                self.put(key, reached)
            elif reached > int(last):
                self.put(key, reached)
                if channel:
                    msg = await channel.send(view=announce_card(
                        f"{game['name']} hit {short(reached)} visits!", "Thanks for playing, flock.", game))
                    await publish(msg)

    @tasks.loop(time=dtime(DIGEST_HOUR, 0, tzinfo=JKT))
    async def digest_loop(self):
        try:
            await self.post_digest()
        except Exception as e:
            log.exception("digest failed")
            await self.bot_log(f"**Daily digest failed:** `{type(e).__name__}: {e}`"[:300])

    @digest_loop.before_loop
    async def _wait_digest(self):
        await self.wait_until_ready()

    @tasks.loop(time=dtime(3, 0, tzinfo=JKT))
    async def backup_loop(self):
        backups = DB_PATH.resolve().parent / "backups"
        backups.mkdir(exist_ok=True)
        dst = sqlite3.connect(backups / f"fs-{datetime.now(JKT):%a}.db")  # keeps the last 7 days
        self.db.backup(dst)
        dst.close()

    async def post_digest(self):
        channel = self.channel("stats")
        if not channel:
            return "No #studio-stats channel yet."
        if not ANALYTICS_KEY:
            return "ROBLOX_ANALYTICS_KEY isn't set yet (it's added once a game is published)."
        # 8-day window: retention for recent days isn't final yet, so each metric shows its latest valid day
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=8)
        blocks = []
        for name, uid in GAMES.items():
            if not uid:
                blocks.append(f"### {name}\nNot published yet.")
                continue
            results = {m: await self.query_metric(uid, m, start, end) for m, _, _ in DIGEST_METRICS}
            blocks.append(digest_text(name, results))
        view = discord.ui.LayoutView().add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## Daily digest · {date.today():%d %b %Y}\n" + "\n\n".join(blocks)),
            accent_colour=PINK))
        await channel.send(view=view)
        return "Posted."


async def publish(msg):
    """Crosspost in announcement channels so following servers get it too."""
    if msg.channel.type == discord.ChannelType.news:
        try:
            await msg.publish()
        except discord.HTTPException:
            log.warning("couldn't publish message %s", msg.id)


async def game_choices(_interaction, current):
    return [app_commands.Choice(name=n, value=n) for n in GAMES if current.lower() in n.lower()][:25]


class AnnounceModal(discord.ui.Modal, title="New announcement"):
    heading = discord.ui.TextInput(label="Title", max_length=100)
    message = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=1800)

    def __init__(self, bot, game_name):
        super().__init__()
        self.bot, self.game_name = bot, game_name

    async def on_submit(self, interaction):
        channel = self.bot.announce_channel(self.game_name)
        if not channel:
            return await interaction.response.send_message("No #announcements channel yet.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        uid = GAMES.get(self.game_name, 0)
        game = (await self.bot.fetch_games()).get(uid) if uid else None
        msg = await channel.send(view=announce_card(self.heading.value, self.message.value, game))
        await publish(msg)
        await interaction.followup.send(f"Posted: {msg.jump_url}", ephemeral=True)


def register_commands(bot):
    @bot.tree.command(description="Live stats for one of our games")
    @app_commands.autocomplete(game=game_choices)
    async def stats(interaction: discord.Interaction, game: str):
        if game not in GAMES:
            return await interaction.response.send_message("Unknown game.", ephemeral=True)
        uid = GAMES[game]
        if not uid:
            return await interaction.response.send_message(view=stats_card(game, None))
        await interaction.response.defer()
        row = (await bot.fetch_games()).get(uid)
        await interaction.followup.send(view=stats_card(game, row, await bot.fetch_icon(uid)))

    @bot.tree.command(description="Post an announcement: studio-wide, or in a game's own #announcements")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(game=game_choices)
    async def announce(interaction: discord.Interaction, game: str = ""):
        if game and game not in GAMES:
            return await interaction.response.send_message("Unknown game.", ephemeral=True)
        await interaction.response.send_modal(AnnounceModal(bot, game or None))

    async def post(interaction, name):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await bot.post_doc(interaction.guild, name)
        except discord.HTTPException as e:
            log.exception("post %s failed", name)
            result = f"Couldn't post: `{e}`"
        await interaction.followup.send(result, ephemeral=True)

    @bot.tree.command(name="post-rules", description="Post rules.md in #rules (replaces the previous copy)")
    @app_commands.default_permissions(manage_guild=True)
    async def post_rules(interaction: discord.Interaction):
        await post(interaction, "rules")

    @bot.tree.command(name="post-welcome", description="Post and pin welcome.md in #general (replaces the previous copy)")
    @app_commands.default_permissions(manage_guild=True)
    async def post_welcome(interaction: discord.Interaction):
        await post(interaction, "welcome")

    @bot.tree.command(name="digest-now", description="Post the analytics digest now")
    @app_commands.default_permissions(manage_guild=True)
    async def digest_now(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await bot.post_digest(), ephemeral=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for var in ("DISCORD_TOKEN", "GUILD_ID"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing {var} (see .env.example)")
    FSBot().run(os.environ["DISCORD_TOKEN"], log_handler=None)
