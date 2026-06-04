# Simple-Sync

A lightweight Python script that pulls every assignment's grade CSV from a
Gradescope course and writes each one to a tab in a Google Sheet. Also syncs
a **Roster** tab with Name, SID, Email, and Role for all enrolled students.

No formulas. No gradebook templates. One tab per assignment, named after the
assignment. Re-runs overwrite cleanly.

## Local setup

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create a Google Cloud service account

1. [Google Cloud Console](https://console.cloud.google.com/) → new or existing project.
2. **APIs & Services → Library** → enable **Google Sheets API**.
3. **APIs & Services → Credentials** → create a Service Account → **Keys → Add Key → JSON**. Download the JSON.
4. Share your target Google Sheet with the service account's `client_email` (Editor).

### 3. `credentials.json`

```bash
cp credentials.example.json credentials.json
```

Fill in your Gradescope email/password and paste the downloaded service account key JSON:

```json
{
  "gradescope_email": "you@example.com",
  "gradescope_password": "...",
  "service_account": { ...paste the entire downloaded key JSON here... }
}
```

`credentials.json` is gitignored.

### 4. `config/custom_sync.json`

```bash
cp config/example.json config/custom_sync.json
```

```json
{
  "GRADESCOPE_COURSE_ID": "1234567",
  "SPREADSHEET_ID": "1AbC..."
}
```

- `GRADESCOPE_COURSE_ID` — last path component of `https://www.gradescope.com/courses/{id}`.
- `SPREADSHEET_ID` — path component of `https://docs.google.com/spreadsheets/d/{id}/edit`.

The Gradescope account must have **Instructor or TA** role on the course.

`config/*.json` is gitignored except `example.json`.

### 5. Run

```bash
python3 simple_sync.py                              # defaults: config/custom_sync.json, credentials.json
python3 simple_sync.py --config my_class.json       # different config
python3 simple_sync.py --credentials other.json     # different credentials
python3 simple_sync.py --sleep 0.5                  # faster writes (risk Sheets 429)
```

---

## Cloud Run deployment (scheduled daily)

`deploy.sh` builds a Docker image, stores secrets in Secret Manager, creates a
Cloud Run Job, and wires it to Cloud Scheduler so the sync runs every day at
6 AM in your timezone. Prerequisites: `gcloud` CLI authenticated, Cloud Billing
enabled on the project.

### 1. Edit `deploy.sh`

Set `PROJECT_ID` and `TIMEZONE` at the top of the file:

```bash
PROJECT_ID="your-gcp-project-id"
TIMEZONE="America/Los_Angeles"   # any IANA timezone
```

### 2. Fill in your local credentials and config (if not done already)

`deploy.sh` uploads `credentials.json` and `config/custom_sync.json` to Secret
Manager on each run, so both files must exist locally before deploying.

### 3. Deploy

```bash
bash deploy.sh
```

This will:
- Enable required GCP APIs
- Create an Artifact Registry repository and build/push the Docker image
- Upload `credentials.json` and `config/custom_sync.json` to Secret Manager
- Create a dedicated service account with read access to both secrets
- Create (or update) the Cloud Run Job with secrets mounted as files
- Create (or update) the Cloud Scheduler trigger at 6 AM daily

The script is idempotent — safe to re-run after config changes or to redeploy
a new image.

### Useful commands

```bash
# Trigger a manual run
gcloud run jobs execute simple-sync --region=us-central1 --project=YOUR_PROJECT

# Tail the logs
gcloud logging read \
  'resource.type=cloud_run_job AND resource.labels.job_name=simple-sync' \
  --project=YOUR_PROJECT --limit=50 --format='value(textPayload)'

# Update credentials (e.g. new semester)
gcloud secrets versions add simple-sync-credentials --data-file="credentials.json"
gcloud secrets versions add simple-sync-config --data-file="config/custom_sync.json"
```

---

## How it works

1. Logs into Gradescope via the public web login (CSRF + `session[*]` form).
2. GETs `/courses/{id}/memberships.csv` and writes a **Roster** tab (Name, SID, Email, Role).
3. GETs `/courses/{id}/assignments` and parses out `{id, title}` pairs.
4. For each assignment, GETs `/courses/{id}/assignments/{aid}/scores.csv` and
   writes it to a tab named after the assignment (creates if missing, clears and
   overwrites on re-runs).

Sheets writes are throttled to ~50/min and 429s are retried with exponential
backoff, so the script stays under the per-user write quota out of the box.
