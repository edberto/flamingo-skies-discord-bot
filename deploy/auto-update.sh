#!/bin/sh
# Auto-deploy, run every 5 minutes by fsbot-update.timer (install: sudo sh setup.sh auto).
# A new commit on main goes live only if test_bot.py passes; if the bot then won't stay up, it rolls back.
# Any problem is written to /opt/fsbot/deploy-note.txt and the log: journalctl -u fsbot-update -n 50
set -u
APP=/opt/fsbot/app
CAND=/opt/fsbot/candidate
PY=/opt/fsbot/venv/bin/python
PIP=/opt/fsbot/venv/bin/pip
NOTE=/opt/fsbot/deploy-note.txt
FAILED=/opt/fsbot/.deploy-failed
asbot() { sudo -u fsbot "$@"; }
note() { printf '%s\n' "$1" | tee -a "$NOTE"; chown fsbot:fsbot "$NOTE"; }

asbot git -C "$APP" fetch -q origin main || { echo "git fetch failed; trying again next time"; exit 0; }
OLD=$(asbot git -C "$APP" rev-parse HEAD)
NEW=$(asbot git -C "$APP" rev-parse origin/main)
[ "$OLD" = "$NEW" ] && exit 0
[ -f "$FAILED" ] && [ "$(cat "$FAILED")" = "$NEW" ] && exit 0  # already rejected this commit; wait for a newer one
DESC=$(asbot git -C "$APP" log -1 --format='%h %s' "$NEW")
CURRENT=$(asbot git -C "$APP" log -1 --format='%h %s' HEAD)

# 1. Check the new version in a separate folder before touching the running one.
asbot git -C "$APP" worktree remove --force "$CAND" 2>/dev/null; rm -rf "$CAND"
asbot git -C "$APP" worktree prune
asbot git -C "$APP" worktree add -q --detach "$CAND" "$NEW"
$PIP install -q -r "$CAND/requirements.txt"
if ! OUT=$(cd "$CAND" && asbot "$PY" test_bot.py 2>&1); then
    echo "$NEW" > "$FAILED"
    asbot git -C "$APP" worktree remove --force "$CAND"
    note "Auto-update to \`$DESC\` skipped: its checks failed. Still running \`$CURRENT\`.
$(printf '%s\n' "$OUT" | tail -n 8)"
    exit 0
fi
asbot git -C "$APP" worktree remove --force "$CAND"

# 2. Switch and restart.
if ! asbot git -C "$APP" merge -q --ff-only "$NEW"; then
    echo "$NEW" > "$FAILED"
    note "Auto-update to \`$DESC\` skipped: GitHub history was rewritten. Run deploy/update.sh by hand."
    exit 0
fi
chown -R fsbot:fsbot /opt/fsbot
BEFORE=$(systemctl show -p NRestarts --value fsbot)
systemctl restart fsbot
sleep 45

# 3. Still up without crash-restarting? Otherwise roll back.
if [ "$(systemctl is-active fsbot)" != active ] || [ "$(systemctl show -p NRestarts --value fsbot)" != "$BEFORE" ]; then
    echo "$NEW" > "$FAILED"
    asbot git -C "$APP" reset -q --hard "$OLD"
    $PIP install -q -r "$APP/requirements.txt"
    chown -R fsbot:fsbot /opt/fsbot
    note "Auto-update to \`$DESC\` crashed on start, so it was rolled back to \`$CURRENT\`. Details: journalctl -u fsbot -n 50"
    systemctl restart fsbot
fi
