"""Run: python3 test_bot.py  (auto-update only deploys a commit if this passes)"""
from bot import (announce_card, digest_text, fmt, latest_point, milestones_passed, open_db, parse_games, short,
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

db = open_db(":memory:")
db.execute("INSERT INTO kv VALUES ('a', '1')")
print("ok")
