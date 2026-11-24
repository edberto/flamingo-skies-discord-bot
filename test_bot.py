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

# ensure_channels: creates what's missing with the right permissions, reuses existing ones, idempotent
class Ch:
    def __init__(self, id, name): self.id, self.name = id, name
class Guild:
    features, default_role, me = ["COMMUNITY"], "everyone", "vesper"
    def __init__(self): self.text_channels, self.created = [Ch(1, "announcements")], []
    def get_channel(self, cid): return next((c for c in self.text_channels if c.id == cid), None)
    async def create_text_channel(self, name, news, reason, overwrites):
        self.created.append((name, news, overwrites)); ch = Ch(100 + len(self.created), name)
        self.text_channels.append(ch); return ch

async def check_channels():
    bot = FSBot.__new__(FSBot); bot.db = open_db(":memory:")
    g = Guild()
    notes = await bot.ensure_channels(g)
    assert [c[0] for c in g.created] == ["playing-now", "studio-stats", "vesper-log"]
    assert notes == ["Created #playing-now", "Created #studio-stats", "Created #vesper-log"]
    status_ow, stats_ow = g.created[0][2], g.created[1][2]
    assert status_ow["everyone"].send_messages is False and status_ow["vesper"].send_messages is True
    assert stats_ow["everyone"].view_channel is False and stats_ow["vesper"].view_channel is True
    assert bot.get("channel:announce") == "1"
    assert await bot.ensure_channels(g) == [] and len(g.created) == 3
asyncio.run(check_channels())
print("ok")
