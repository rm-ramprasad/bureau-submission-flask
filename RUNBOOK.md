# Runbook

Day-to-day operation of the bureau submission platform. Commands run from the project root with the virtualenv.

## Environment

| Variable | Purpose |
|---|---|
| `SUBMISSIONS_DATABASE_URL` | App database, e.g. `mysql+pymysql://user:pass@host:3306/my_demo_db` (URL-encode special characters in the password) |
| `SUBMISSIONS_SOURCE_DATABASE_URL` | LMS server with the NBFC schemas, if it differs from the app database |
| `SUBMISSIONS_STORAGE` | `s3` (real use) or `local` (development; default). **Use the same storage settings for web, worker and retention** |
| `SUBMISSIONS_S3_BUCKET` | Bucket for generated files (required for `s3`) |
| `SUBMISSIONS_S3_PREFIX` | Key prefix, default `bureau-submissions`. Files go to `s3://<bucket>/<prefix>/<tenant>/<batch>/<file>` |
| `SUBMISSIONS_S3_REGION` | Bucket region (optional: else from the AWS config/environment) |
| `SUBMISSIONS_S3_KMS_KEY_ID` | Optional KMS key for SSE-KMS. Without it, objects use SSE-S3 (AES256) |
| `SUBMISSIONS_OUTPUT_DIR` | Local file store root when `SUBMISSIONS_STORAGE=local` (default `output`) |
| `SUBMISSIONS_SECRET_KEY` | Signs UI sessions and form tokens. Long random string, keep it secret |
| `SUBMISSIONS_LOG_LEVEL` | `INFO` (default) or `DEBUG` |

## Processes

| Process | Command | Notes |
|---|---|---|
| Web UI | `python -m submissions.web --host 0.0.0.0 --port 5000` | Development server. On EC2, the Docker image runs it under gunicorn on port 8000 (see "Deploying on EC2") |
| Worker | `python -m submissions.worker` | Polls every 5 s. Run one or more; claiming is safe across workers. Give each a distinct `--id` |

**If runs stay PENDING:** the UI shows **"No worker running"** when no worker has polled in the last 30 seconds. Start a worker.

## Generated files on S3

- **Where they go:** each GENERATE row writes the bureau file and its `.exceptions.csv` to S3. The full `s3://…` URI is stored on `submission_request` (`output_file_path`, `exceptions_file_path`).
- **No overwrites:** writes are conditional (`If-None-Match: *`), so an existing object is never replaced.
- **Downloads:** the UI streams them from S3 through the app, so the bucket stays private and users need no AWS access. Each download is audited with its S3 location.
- **Retention** deletes the objects.
- **Credentials** come from the standard AWS chain: IAM role on EC2/ECS (preferred), environment, or profile. Never put keys in files.

**Bucket setup:**
- Block all public access.
- Default encryption on.
- Optional: versioning and lifecycle rules as a second safety net.

The web, worker and retention role needs this least-privilege policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
     "Resource": "arn:aws:s3:::<bucket>/bureau-submissions/*"}
  ]
}
```

With SSE-KMS, also allow `kms:GenerateDataKey` and `kms:Decrypt` on that key.

**Current EC2 (`i-080783c59045ca2ca`):**
- **Shared role:** it uses the shared role `analytics-bi-demo-InstanceRole-…`. Our permissions are in the inline policy `bureau-s3-permission`.
- **Exceptions in a second policy:** the role's `analytics-query-runtime` policy denies S3 writes and deletes. Its statements `DenyS3WritesExceptAthenaResultsAndBureauFiles` and `DenyS3DeletesExceptBureauFiles` exclude `opta-s3-demo/bureau-submissions/*`.
- **If someone edits that policy and drops the exception:** file generation fails with `AccessDenied ... explicit deny in an identity-based policy`, and retention can't delete files.

## Scheduled tasks

Use Windows Task Scheduler (or cron). Each task runs one command and exits.

| When | Command |
|---|---|
| Month end, e.g. 2nd of each month 06:00 | `python -m submissions.ops.schedule --cycle ME` |
| Weekly: 10th, 17th and 24th | `python -m submissions.ops.schedule --cycle W1` (then `W2`, `W3`) |
| Nightly 02:00 | `python -m submissions.ops.retention --days 90` |

**Schedule options:**
- The scheduler only creates **Validate** runs (always validate first). The team reviews findings, captures exceptions, then uses "Generate files from this validation" in the UI.
- `--tenant nbfc_001` limits a run to one NBFC (repeatable).
- `--as-of YYYY-MM-DD` overrides the as-of date.
- Re-running a schedule never creates duplicates of runs that are still pending or running.

Example Windows task action: program `D:\...\submissions-extract\.venv\Scripts\python.exe`, arguments `-m submissions.ops.schedule --cycle ME`, start in `D:\...\submissions-extract`. Set the environment variables at user or system level.

## Automatic recovery (built into the worker)

On every poll the worker:
- **Stuck rows:** puts RUNNING rows older than 2 hours back to PENDING. After 3 attempts the row becomes FAILED ("Timed out…").
- **Abandoned snapshots:** marks snapshots stuck in BUILDING/EXTRACTED for 2 hours as FAILED and removes their rows.
- **Failed snapshots on retry:** rebuilds a batch's failed snapshot when a row is retried. The retry does not just repeat the old error.

Recovery actions appear in **Audit** (actor `recovery`).

## Retention

`python -m submissions.ops.retention --days 90 [--dry-run]`

- **Removed:** for snapshots older than N days, the canonical rows, findings and record outcomes. Plus generated files (inside the output folder only) for requests finished before the cutoff.
- **Kept:** the snapshot header and `validation_summary`, for trends.
- **Status:** the snapshot becomes PURGED, and "Generate from this validation" is refused for it.

## Audit

The UI **Audit** page (or `audit_event` table) records:
- run creation, generate-from-validation, retry, cancel and file downloads (with the user);
- scheduled runs;
- recovery and purges.

## Troubleshooting

| Symptom | Cause / action |
|---|---|
| Runs stay PENDING, red banner | No worker running: start `python -m submissions.worker` |
| `Unknown database 'nbfc_xxx'` | The NBFC schema is not on the server the worker reads. Check `SUBMISSIONS_SOURCE_DATABASE_URL` and the tenant's `source_schema` |
| `Snapshot build failed: <entity>: ...` | Mapping SQL error. Run `python -m submissions.adapters.check clients/<tenant>/mapping.yaml --schema <schema>`, fix, then **Retry** (the snapshot is rebuilt) |
| `No bureau profile for …` | Add the bureau's profile for that NBFC (`bureau_profile`) |
| Download link missing or 404 | The file was purged by retention, the S3 object was deleted, or web and worker use different storage settings (bucket/prefix) |
| Generate fails with `AccessDenied` | The role lacks `s3:PutObject` on the bucket/prefix (or KMS permissions with SSE-KMS) |
| Generate fails with `NoCredentialsError` | No AWS credentials on the worker host: attach an IAM role or configure a profile |
| `Missing or invalid form token` | The page was open too long or the secret key changed. Reload and resubmit |
| Many `UCRF.ENUM.*` findings | Client codes missing from the mapping lookups. `python -m submissions.onboarding.dry_run --tenant <id>` lists them |

## Deploying on EC2 (Docker)

Internal use (about 5 users): one small instance runs the web UI and the worker as two containers from the same image, and the host's cron runs the scheduled tasks. Files: [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml), [deploy/](deploy/).

**1. AWS setup (once)**
- **IAM role** `submissions-ec2`: the policy in [deploy/iam-policy.json](deploy/iam-policy.json) plus the managed policy `AmazonSSMManagedInstanceCore` (lets you connect through Session Manager, so no SSH key or port 22).
- **Instance:** Amazon Linux 2023, `t3.small`, 20 GB disk, private subnet, attach the role.
  - **Metadata:** IMDSv2 required, **hop limit 2**. Containers are one network hop further away. With the default limit of 1, they cannot get the role's credentials (`NoCredentialsError`).
  - To fix an existing instance: `aws ec2 modify-instance-metadata-options --instance-id <id> --http-tokens required --http-put-response-hop-limit 2`
- **Security groups:**
  - Instance: allow inbound TCP 8000 from the office/VPN range only.
  - MySQL: allow 3306 from the instance's security group.

**2. Server setup (once).** Connect with Session Manager, then run `sudo su - ec2-user` and `bash setup-ec2.sh` (copy [deploy/setup-ec2.sh](deploy/setup-ec2.sh) over first). The script:
- installs Docker, cron and automatic security updates;
- creates `/opt/submissions`.

Then log out and back in.

**3. First deploy.** Releases travel through S3: [deploy/package.ps1](deploy/package.ps1) zips the code on your laptop and uploads it to `s3://opta-s3-demo/deploy/`. On the server, [deploy/update.sh](deploy/update.sh) downloads a release, rebuilds the image, restarts the containers and applies migrations. `env.ps1` and the venv are never included.
1. **Laptop:** run `aws login`, then from the project root `.\deploy\package.ps1`. Note the release name it prints, e.g. `submissions-20261022-101500`.
2. **Server, settings file:** create `/opt/submissions/.env` using the variables in [deploy/env.example](deploy/env.example), then run `chmod 600 /opt/submissions/.env`.
   - Fill in the database URL.
   - Generate a new secret key on the server: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. **Server, first install** (`update.sh` isn't on the server yet, so take it from the release):
   ```bash
   R=submissions-20261022-101500
   aws s3 cp s3://opta-s3-demo/deploy/$R.zip /tmp/$R.zip && unzip -o -q /tmp/$R.zip deploy/update.sh -d /tmp/$R
   bash /tmp/$R/deploy/update.sh $R
   ```
4. **Install the schedule:** `crontab /opt/submissions/deploy/crontab`.
5. **Check:** browse to `http://<instance-private-ip>:8000`. The "No worker running" banner should not show.
   - If the build fails, run `docker compose build` in `/opt/submissions` and read its output.

**4. Updates**
1. **Laptop:** `.\deploy\package.ps1`.
2. **Server:** `bash /opt/submissions/deploy/update.sh <release name>`.
   - This takes about a minute of downtime.
   - It keeps `.env`, applies pending migrations, and doesn't touch S3 files.
   - `cat /opt/submissions/RELEASE` shows what's running.

**Versions:** Python (3.14.5, in the [Dockerfile](Dockerfile)) and every library ([requirements.lock](requirements.lock)) are pinned inside the image. The server's own Python is never used, so OS updates on the instance cannot break the app. To upgrade a version, change it there, run the tests, then rebuild.

**Amazon Linux 2023 updates:**
- AL2023 locks each instance to a repository release. The nightly `dnf-automatic` security updates only apply fixes within that release.
- Every few months, in a quiet window, move to the latest release: `sudo dnf upgrade --releasever=latest -y && sudo reboot`. The containers start again by themselves.
- Neither step touches the app's Python, which is inside the image.

**Rollback:** run `update.sh` with the previous release name (list them with `aws s3 ls s3://opta-s3-demo/deploy/`). Migrations are not undone, so roll back only to a release built against the same database version.

**Everyday commands** (run in `/opt/submissions`):

| Task | Command |
|---|---|
| Status | `docker compose ps` |
| Logs | `docker compose logs -f web` / `docker compose logs -f worker --since 1h` |
| Restart | `docker compose restart worker` |
| Stop / start everything | `docker compose down` / `docker compose up -d` |
| Scheduled task output | `tail -100 /var/log/submissions-cron.log` |
| One-off command | `docker compose run --rm worker python -m submissions.ops.retention --days 90 --dry-run` |

**Notes:**
- Containers restart automatically after a crash or reboot.
- Logs rotate at 5 × 20 MB per container.
- The instance holds no data (everything is in MySQL and S3). If it's lost, launch a new one and repeat steps 2–3 with the same `.env` values.
- Keep a copy of `.env` somewhere safe, such as a password manager or SSM Parameter Store. Without the same secret key, open UI sessions log out; nothing else is affected.

## Database changes

Apply new migrations with `python -m submissions.migrate`. If you run a migration file by hand, record it:
`INSERT INTO schema_migration VALUES ('<file name without .sql>', UTC_TIMESTAMP());`
