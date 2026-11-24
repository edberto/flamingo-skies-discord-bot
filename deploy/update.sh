#!/bin/sh
# Update the bot from GitHub by hand:  sudo sh /opt/fsbot/app/deploy/update.sh
set -e
sudo -u fsbot git -C /opt/fsbot/app pull --ff-only
/opt/fsbot/venv/bin/pip install -q -r /opt/fsbot/app/requirements.txt
chown -R fsbot:fsbot /opt/fsbot
systemctl restart fsbot
echo "Updated to: $(sudo -u fsbot git -C /opt/fsbot/app log -1 --format='%h %s')"
