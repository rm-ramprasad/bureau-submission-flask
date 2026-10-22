#!/usr/bin/env bash
# One-time setup of a fresh Amazon Linux 2023 instance. Run as ec2-user: bash setup-ec2.sh
set -euo pipefail

# Docker + compose plugin, cron, automatic security updates
sudo dnf install -y docker cronie dnf-automatic unzip
# The compose plugin is not in the AL2023 repositories; install it from Docker's releases (skipped if present)
if ! docker compose version >/dev/null 2>&1; then
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
sudo systemctl enable --now docker crond
sudo usermod -aG docker ec2-user

sudo sed -i 's/^upgrade_type.*/upgrade_type = security/; s/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
sudo systemctl enable --now dnf-automatic.timer

# App folder and cron log
sudo mkdir -p /opt/submissions && sudo chown ec2-user: /opt/submissions
sudo touch /var/log/submissions-cron.log && sudo chown ec2-user: /var/log/submissions-cron.log

echo "Done. Log out and back in (docker group), then copy the code to /opt/submissions and create .env."
