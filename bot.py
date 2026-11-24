"""Flamingo Skies studio bot.

- Every 5 min: public Roblox stats for each game -> one live "playing now" card in STATUS_CHANNEL_ID,
  and a post in ANNOUNCE_CHANNEL_ID when a game passes a visit milestone (1K, 2.5K, 5K, 10K, ...).
- Daily at DIGEST_HOUR Asia/Jakarta: private analytics digest (Open Cloud Analytics, Beta) in STATS_CHANNEL_ID.
- /stats: a game's card. /announce (Manage Server): release post with a Play button. /digest-now (Manage Server).
A game whose universe ID is 0 is a placeholder: it shows as "not published yet" and is skipped by the loops.
"""
import asyncio
import logging
import os
import sqlite3
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
STATUS_CHANNEL_ID = int(os.environ.get("STATUS_CHANNEL_ID", "0"))
ANNOUNCE_CHANNEL_ID = int(os.environ.get("ANNOUNCE_CHANNEL_ID", "0"))
STATS_CHANNEL_ID = int(os.environ.get("STATS_CHANNEL_ID", "0"))
ANALYTICS_KEY = os.environ.get("ROBLOX_ANALYTICS_KEY", "")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "9"))
DB_PATH = Path(os.environ.get("DB_PATH", "fs.db"))

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


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    return db


class FSBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())  # no privileged intents
        self.tree = app_commands.CommandTree(self)
        self.db = open_db()
        self.http_session = None

    def get(self, k):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, str(v)))
        self.db.commit()

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        register_commands(self)
        guild = discord.Object(GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.stats_loop.start()
        self.digest_loop.start()

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

    async def update_status(self, games):
        channel = self.get_channel(STATUS_CHANNEL_ID)
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
        channel = self.get_channel(ANNOUNCE_CHANNEL_ID)
        for uid, game in games.items():
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
        except Exception:
            log.exception("digest failed")

    @digest_loop.before_loop
    async def _wait_digest(self):
        await self.wait_until_ready()

    async def post_digest(self):
        channel = self.get_channel(STATS_CHANNEL_ID)
        if not channel or not ANALYTICS_KEY:
            return "STATS_CHANNEL_ID or ROBLOX_ANALYTICS_KEY isn't set."
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

    def __init__(self, bot, universe_id):
        super().__init__()
        self.bot, self.universe_id = bot, universe_id

    async def on_submit(self, interaction):
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if not channel:
            return await interaction.response.send_message("ANNOUNCE_CHANNEL_ID isn't set.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        game = (await self.bot.fetch_games()).get(self.universe_id) if self.universe_id else None
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

    @bot.tree.command(description="Post an announcement (with a Play button if you pick a game)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.autocomplete(game=game_choices)
    async def announce(interaction: discord.Interaction, game: str = ""):
        await interaction.response.send_modal(AnnounceModal(bot, GAMES.get(game, 0)))

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
