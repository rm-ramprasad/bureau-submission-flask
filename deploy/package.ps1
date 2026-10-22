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
$stage = (New-Item -ItemType Directory $stage).FullName     # long path form: TEMP can be a short 8.3 name
$zip = "$stage.zip"
foreach ($item in $include) { Copy-Item (Join-Path $root $item) $stage -Recurse }
Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
# Build the zip by hand with forward-slash entry names: Windows PowerShell 5.1's Compress-Archive writes
# backslashes, which Linux unzips as literal file names like "deploy\setup-ec2.sh"
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::Open($zip, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in Get-ChildItem $stage -Recurse -File) {
        $entry = $file.FullName.Substring($stage.Length + 1).Replace('\', '/')
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $entry) | Out-Null
    }
} finally {
    $archive.Dispose()
}

aws s3 cp $zip "s3://opta-s3-demo/deploy/$name.zip" --region ap-south-1
Remove-Item $stage, $zip -Recurse -Force
Write-Host "Uploaded release $name. On the server run: bash /opt/submissions/deploy/update.sh $name"
