# Simple-Sync

A lightweight Python script that pulls every assignment's grade CSV from a
Gradescope course and writes each one — as-is — to a tab in a Google Sheet.

No formulas. No gradebook templates. One tab per assignment, named after the
assignment. Re-runs overwrite cleanly.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create a Google Cloud service account

1. [Google Cloud Console](https://console.cloud.google.com/) → new project (or reuse one).
2. **APIs & Services → Library** → enable **Google Sheets API**.
3. **APIs & Services → Credentials** → create a Service Account → **Keys → Add Key → JSON**. Download the JSON.
4. Share your target Google Sheet with the service account's `client_email` (Editor).

### 3. `credentials.json`

Copy `credentials.example.json` to `credentials.json` and fill it in:

```json
{
  "gradescope_email": "you@example.com",
  "gradescope_password": "...",
  "service_account": { ...paste the entire downloaded key JSON object here... }
}
```

`credentials.json` is gitignored.

### 4. `config/<your_class>.json`

```json
{
  "GRADESCOPE_COURSE_ID": "1234567",
  "SPREADSHEET_ID": "1AbC..."
}
```

- `GRADESCOPE_COURSE_ID` — last path component of `https://www.gradescope.com/courses/{id}`.
- `SPREADSHEET_ID` — path component of `https://docs.google.com/spreadsheets/d/{id}/edit`.

The Gradescope account must have **Instructor or TA** role on the course (the
scores CSV export endpoint requires staff access).

`config/*.json` is gitignored except `example.json`.

## Run

```bash
python3 simple_sync.py                              # defaults: config/custom_sync.json, credentials.json
python3 simple_sync.py --config my_class.json       # different config
python3 simple_sync.py --credentials other.json     # different credentials
python3 simple_sync.py --sleep 0.5                  # faster (risk Sheets 429)
```

The script throttles to ~50 Sheets writes/min and retries 429s with exponential
backoff, so it stays under the per-user write quota out of the box.

## How it works

1. Logs into Gradescope via the public web login (CSRF + `session[*]` form).
2. GETs `/courses/{id}/assignments` and parses out `{id, title}` pairs.
3. For each assignment, GETs `/courses/{id}/assignments/{aid}/scores.csv`.
4. Writes the CSV to a tab in the target Sheet (creates the tab if missing,
   clears existing content first).

That's the whole thing — ~200 lines.
