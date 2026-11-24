"""Run: python3 test_bot.py  (auto-update only deploys a commit if this passes)"""
import asyncio

from bot import (FSBot, announce_card, digest_text, fmt, latest_point, milestones_passed, open_db, parse_games, short,
                 stats_card, status_card)

assert parse_games("Huss game:0, Other Game: 123") == {"Huss game": 0, "Other Game": 123}
assert parse_games("Name: With: Colons:42") == {"Name: With: Colons": 42}

assert [milestones_passed(v) for v in (0, 999, 1000, 2499, 2500, 9999, 10000, 260_000, 1_000_000)] == \
       [0, 0, 1000, 1000, 2500, 5000, 10000, 250_000, 1_000_000]
assert [short(n) for n in (950, 1000, 2500, 1_250_000, 3_000_000_000)] == ["950", "1K", "2.5K", "1.25M", "3B"]
assert fmt(0.123, "pct") == "12.3%" and fmt(None, "n") == "—" and fmt(1234.4, "rbx") == "R$ 1,234"

done = {"done": True, "response": {"values": [{"breakdowns": [], "dataPoints": [
    {"time": "2026-11-20T00:00:00Z", "value": 10}, {"time": "2026-11-21T00:00:00Z", "value": 12},
    {"time": "2026-11-22T00:00:00Z", "value": None}]}]}}
assert latest_point(done) == ("2026-11-21", 12)
assert latest_point({"error": {"code": 2001}}) == (None, None)
assert "D1 retention: **40.0%** (2026-11-21)" in digest_text("Huss", {"ForwardD1Retention": ("2026-11-21", 0.4)})

# Components V2 views build (catches discord.py API mistakes before they reach the server)
game = {"id": 1, "name": "Huss", "playing": 12, "visits": 3400, "favoritedCount": 5, "rootPlaceId": 99}
for view in (stats_card("Huss", None), stats_card("Huss", game), stats_card("Huss", game, "https://x/i.png"),
             status_card([("Huss", game), ("Next", None)]), announce_card("Hi", "Body"), announce_card("Hi", "B", game)):
    assert view.to_components(), view

# ensure_layout: creates what's missing with the right visibility, reuses existing things, idempotent
class Obj:
    def __init__(self, id, name, **kw): self.id, self.name, self.channels, self.overwrites = id, name, [], {}; self.__dict__.update(kw)
class Guild:
    features, default_role, me = ["COMMUNITY"], "everyone", "vesper"
    def __init__(self):
        self.made, self.roles = [], [Obj(1, "Moderator")]  # Moderator already exists
        hub = Obj(3, "FLAMINGO SKIES"); hub.channels = [Obj(2, "announcements")]  # and so does the hub's #announcements
        self.categories, self.channels = [hub], list(hub.channels)
    def get_channel(self, cid): return next((c for c in self.channels if c.id == cid), None)
    def _new(self, kind, name, category=None, overwrites=None, **kw):
        o = Obj(100 + len(self.made), name, kind=kind, overwrites=overwrites or {}, **kw)
        self.made.append(o)
        if category: category.channels.append(o)
        return o
    async def create_role(self, name, **kw): r = self._new("role", name, **kw); self.roles.append(r); return r
    async def create_category(self, name, overwrites, reason):
        c = self._new("category", name, overwrites=overwrites); self.categories.append(c); return c
    async def create_text_channel(self, name, category, news, overwrites, reason):
        c = self._new("news" if news else "text", name, category, overwrites); self.channels.append(c); return c
    async def create_forum(self, name, category, overwrites, reason):
        c = self._new("forum", name, category, overwrites); self.channels.append(c); return c

async def check_layout():
    import bot as b
    b.GAMES = {"Huss game": 0, "Mahjong Mayhem": 0}
    vesper = FSBot.__new__(FSBot); vesper.db = open_db(":memory:")
    g = Guild()
    notes = await vesper.ensure_layout(g)
    made = {(o.kind, o.name) for o in g.made}
    assert ("role", "Moderator") not in made and {("role", "Dev"), ("role", "Huss game"), ("role", "huss-game-expert"),
                                                   ("role", "Flamingo"), ("role", "Tester")} <= made
    assert vesper.get("channel:announce") == "2" and ("category", "FLAMINGO SKIES") not in made  # reused
    game_news = [o for o in g.made if o.name == "announcements"]
    assert [o.kind for o in game_news] == ["news", "news"]  # one per game, not a duplicate hub one
    assert vesper.get("channel:announce:Mahjong Mayhem") == str(game_news[1].id)
    assert {("category", "MAHJONG MAYHEM"), ("role", "mahjong-mayhem-expert"), ("role", "Mahjong Mayhem")} <= made
    assert {("text", "bot-commands"), ("forum", "suggestions"), ("forum", "bug-report"), ("text", "media")} <= made
    by = {o.name: o for o in g.made}
    role = {r.name: r for r in g.roles}
    hide = by["HUSS GAME"].overwrites
    assert hide["everyone"].view_channel is False and hide["vesper"].view_channel is True
    assert all(hide[role[n]].view_channel for n in ("Huss game", "huss-game-expert", "Moderator", "Dev"))
    assert role["Flamingo"] not in hide and role["Tester"] not in hide
    top = by["top-suggestions"].overwrites  # staff only: Moderator and Dev, not the game or expert roles
    assert top["everyone"].view_channel is False and top[role["Moderator"]].view_channel and top[role["Dev"]].view_channel
    assert role["Huss game"] not in top and role["huss-game-expert"] not in top
    admin = by["ADMIN"].overwrites
    assert admin["everyone"].view_channel is False and role["Moderator"] not in admin and role["Dev"] not in admin
    assert by["studio-stats"].overwrites["everyone"].view_channel is False  # inherits the ADMIN lock
    assert by["playing-now"].overwrites["everyone"].send_messages is False and by["bot-commands"].overwrites == {}
    news_ow = game_news[0].overwrites
    assert news_ow["everyone"].send_messages is False and news_ow[role["Moderator"]].send_messages
    assert news_ow[role["Dev"]].send_messages and news_ow["everyone"].view_channel is False  # still game-gated
    assert notes and await vesper.ensure_layout(g) == []  # second run changes nothing
asyncio.run(check_layout())
print("ok")
