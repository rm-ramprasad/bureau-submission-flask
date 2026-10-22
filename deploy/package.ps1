# Package a release and upload it to S3 for the EC2 server to pick up (run from the project root after `aws login`):
#   .\deploy\package.ps1
# Then on the server:  bash /opt/submissions/deploy/update.sh <release name printed below>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$name = "submissions-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$stage = Join-Path $env:TEMP $name
$zip = "$stage.zip"

# Only what the image and the server need: no venv, tests, local output or env.ps1 (it holds the password)
$include = "Dockerfile", ".dockerignore", "docker-compose.yml", "pyproject.toml", "requirements.lock",
           "submissions", "clients", "migrations", "deploy", "RUNBOOK.md"
New-Item -ItemType Directory $stage | Out-Null
foreach ($item in $include) { Copy-Item (Join-Path $root $item) $stage -Recurse }
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Compress-Archive -Path "$stage\*" -DestinationPath $zip

aws s3 cp $zip "s3://opta-s3-demo/deploy/$name.zip" --region ap-south-1
Remove-Item $stage, $zip -Recurse -Force
Write-Host "Uploaded release $name. On the server run: bash /opt/submissions/deploy/update.sh $name"
