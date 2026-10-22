#!/usr/bin/env bash
# Deploy a release uploaded by deploy/package.ps1. Run as ec2-user:
#   bash /opt/submissions/deploy/update.sh submissions-YYYYMMDD-HHMMSS
# Keeps .env, rebuilds the image, restarts the containers, applies pending migrations.
# Rollback: run it again with the previous release name.
set -euo pipefail
release="${1:?usage: update.sh <release name>}"
app=/opt/submissions
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

aws s3 cp "s3://opta-s3-demo/deploy/$release.zip" "$tmp/release.zip" --region ap-south-1
unzip -q "$tmp/release.zip" -d "$tmp/code"

# Replace the code, keep the server's settings
find "$app" -mindepth 1 -maxdepth 1 ! -name .env -exec rm -rf {} +
cp -r "$tmp/code/." "$app/"
echo "$release" > "$app/RELEASE"

cd "$app"
docker compose up -d --build
docker compose run --rm -T worker python -m submissions.migrate
docker image prune -f >/dev/null
docker compose ps
echo "Deployed $release"
