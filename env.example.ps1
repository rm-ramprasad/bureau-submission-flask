# Copy to env.ps1, fill in, and load it in EVERY terminal before starting the web app or the worker:
#   . .\env.ps1
# The web app, worker and scheduled tasks must share these settings (same database, same file storage).
# env.ps1 holds a password: keep it out of source control.

$env:SUBMISSIONS_DATABASE_URL  = "mysql+pymysql://<user>:<url-encoded-password>@<host>:3306/my_demo_db"
$env:SUBMISSIONS_SECRET_KEY    = "<long random string>"

# Generated files: AWS S3 (credentials from `aws login` locally, the EC2 instance role on the server)
$env:SUBMISSIONS_STORAGE       = "s3"
$env:SUBMISSIONS_S3_BUCKET     = "opta-s3-demo"
$env:SUBMISSIONS_S3_PREFIX     = "bureau-submissions"
$env:SUBMISSIONS_S3_REGION     = "ap-south-1"
