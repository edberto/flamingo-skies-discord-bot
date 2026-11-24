#!/bin/sh
# One-time server setup for the Flamingo Skies bot, next to the Icarus clan bot on the same e2-micro.
#   sudo sh setup.sh install  -> fsbot user, venv, swap, memory caps for both bots, fsbot.service
#   sudo sh setup.sh key      -> prints a key; add it on GitHub as a read-only deploy key
#   sudo sh setup.sh clone    -> clones the repo and starts the bot (put .env in /opt/fsbot first)
#   sudo sh /opt/fsbot/app/deploy/setup.sh auto|manual -> GitHub auto-deploy every 5 min on/off
set -e
REPO=git@github.com:edberto/flamingo-skies-discord-bot.git
case "$1" in
  install)
    apt-get install -y -q git python3-venv >/dev/null
    id fsbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/fsbot fsbot
    [ -d /opt/fsbot/venv ] || python3 -m venv /opt/fsbot/venv
    # 1 GB RAM is tight for two Python bots: add 1 GB swap once
    if ! swapon --show | grep -q /swapfile; then
      fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
      grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    # cap each bot so one runaway can't take the other down
    for svc in clanbot fsbot; do
      mkdir -p /etc/systemd/system/$svc.service.d
      printf '[Service]\nMemoryHigh=300M\nMemoryMax=400M\n' > /etc/systemd/system/$svc.service.d/memory.conf
    done
    cat > /etc/systemd/system/fsbot.service <<'UNIT'
[Unit]
Description=Flamingo Skies bot
After=network-online.target
Wants=network-online.target

[Service]
User=fsbot
WorkingDirectory=/opt/fsbot
EnvironmentFile=/opt/fsbot/.env
ExecStart=/opt/fsbot/venv/bin/python /opt/fsbot/app/bot.py
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/fsbot
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl is-active -q clanbot && systemctl restart clanbot  # apply its new memory cap
    chown -R fsbot:fsbot /opt/fsbot
    echo "Installed. Next: sudo sh setup.sh key" ;;
  key)
    sudo -u fsbot mkdir -p /opt/fsbot/.ssh
    [ -f /opt/fsbot/.ssh/id_ed25519 ] || sudo -u fsbot ssh-keygen -q -t ed25519 -N "" -C fsbot-server -f /opt/fsbot/.ssh/id_ed25519
    echo; echo "Add this as a deploy key (read-only) on GitHub:"; echo
    cat /opt/fsbot/.ssh/id_ed25519.pub ;;
  clone)
    [ -f /opt/fsbot/.env ] || { echo "Put your .env in /opt/fsbot/.env first (see .env.example)"; exit 1; }
    sudo -u fsbot env GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git clone -q "$REPO" /opt/fsbot/app
    /opt/fsbot/venv/bin/pip install -q -r /opt/fsbot/app/requirements.txt
    chown -R fsbot:fsbot /opt/fsbot && chmod 600 /opt/fsbot/.env
    systemctl enable --now fsbot
    echo "Running. Log: journalctl -u fsbot -f" ;;
  auto)
    cat > /etc/systemd/system/fsbot-update.service <<'UNIT'
[Unit]
Description=Deploy new Flamingo Skies bot commits from GitHub (tested, with rollback)

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'cp /opt/fsbot/app/deploy/auto-update.sh /run/fsbot-auto-update.sh && exec /bin/sh /run/fsbot-auto-update.sh'
UNIT
    cat > /etc/systemd/system/fsbot-update.timer <<'UNIT'
[Unit]
Description=Check GitHub for Flamingo Skies bot updates every 5 minutes

[Timer]
OnBootSec=4min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
    systemctl daemon-reload && systemctl enable --now fsbot-update.timer
    echo "Auto-update is on. Log: journalctl -u fsbot-update -n 50" ;;
  manual)
    systemctl disable --now fsbot-update.timer && echo "Auto-update is off. Update by hand with deploy/update.sh" ;;
  *) echo "usage: sudo sh setup.sh install|key|clone|auto|manual"; exit 1 ;;
esac
