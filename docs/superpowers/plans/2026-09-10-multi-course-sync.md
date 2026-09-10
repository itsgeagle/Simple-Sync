# Multi-course Gradescope Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync Gradescope → Google Sheets for CS 61A, CS 10 and CS 61C Fall 2026 from one daily job, snapshot each due assignment's CSV into a per-course Drive folder, and retire the broken duplicate job.

**Architecture:** Both `simple_sync.py` and `deadline_export.py` gain a `normalize_courses()` config loader that accepts a new `courses` array *and* the current flat single-course shape, plus a per-course loop with try/except isolation and a non-zero exit if any course fails. Courses run serially to stay inside the Sheets 60-writes/min service-account quota.

**Tech Stack:** Python 3.12, `gspread`, `requests`, `beautifulsoup4`, `google-auth`, `google-api-python-client`, `firebase-admin`; Cloud Run Jobs + Cloud Scheduler + Secret Manager on GCP project `autoremind-480200`.

**Spec:** `docs/superpowers/specs/2026-09-10-multi-course-sync-design.md`

## Global Constraints

- GCP project `autoremind-480200`, region `us-central1`, timezone `America/Los_Angeles`.
- Gradescope account: `autoremindberkeley@gmail.com`.
- Sheets service account: `gradesync@autoremind-480200.iam.gserviceaccount.com`.
- Drive OAuth identity: `autoremind@berkeley.edu`.
- Fall 2026 courses — these exact values:
  | code | gradescope_course_id | spreadsheet_id |
  |---|---|---|
  | `CS61A` | `1370542` | `1kWZlV__nnG__wgUoBDTyXrZk9LeJmggvofXIFJ7TUv8` |
  | `CS10` | `1371156` | `1ctQJcUUAsYDKEDQdUZJf3-yEyDMcg7IC6CJiHPK7pp8` |
  | `CS61C` | `1365362` | `14Y8cNWI8bEGaf8l8a_eEliM8ioiqSWoH4UZr0fL-H3o` |
- Parent Drive folder: `1UT3DlzNlgst1SA5YohXirreC0xaXsw_h` (`[fa26] autoremind sheets`).
- Do **not** touch `autoremind-daily-job`, `gradesync_to_db.py`, or Summer sheet `1HPaMeAudhiOXNZ-JfGr5Viw9qpoaZWRn-kAuOX6gGx8`.
- Legacy flat config shape (`GRADESCOPE_COURSE_ID` + `SPREADSHEET_ID`) must keep working.
- Commit messages end with the two attribution lines used in this repo's `ccebe5d`.

## File Structure

| File | Responsibility |
|---|---|
| `Simple-Sync/simple_sync.py` | Add `normalize_courses()`, `sync_course()`, multi-course `main()` |
| `Simple-Sync/tests/test_config.py` | **new** — config normalisation + isolation tests |
| `Simple-Sync/config/example.json` | Document the `courses` array shape |
| `Simple-Sync/deploy.sh` | Task timeout 60m; unchanged secret wiring |
| `deadline-export/deadline_export.py` | Add `normalize_courses()`, `export_course()`, `--date`, multi-course `main()` |
| `deadline-export/tests/test_logic.py` | Extend with config + date-selection tests |
| `deadline-export/config/example.json` | Document the `courses` array shape |
| `remind/services/shared/courses.json` | Add `gradescope_course_id`, `drive_folder_id` |

`normalize_courses()` is deliberately duplicated in the two repos — they are independently deployed and share no package. The shapes must stay identical.

---

### Task 1: Create the three Drive subfolders

**Files:**
- Create: `/private/tmp/claude-501/-Users-aaryanmehta-PythonProjects-Simple-Sync/135d17a2-393e-468e-b900-3e1a7e119d61/scratchpad/make_folders.py`

**Interfaces:**
- Consumes: nothing
- Produces: three Drive folder IDs, referenced by every later config task as `<CS61A_FOLDER>`, `<CS10_FOLDER>`, `<CS61C_FOLDER>`

- [ ] **Step 1: Write the folder-creation script (idempotent — reuses a folder if the name already exists)**

```python
import json
from google.oauth2.credentials import Credentials as UserCreds
from googleapiclient.discovery import build

PARENT = "1UT3DlzNlgst1SA5YohXirreC0xaXsw_h"
creds = json.load(open("/Users/aaryanmehta/PythonProjects/deadline-export/credentials.json"))
ou = creds.get("drive_oauth") or json.load(
    open("/Users/aaryanmehta/PythonProjects/deadline-export/drive_oauth.json"))
svc = build("drive", "v3", credentials=UserCreds.from_authorized_user_info(
    ou, scopes=["https://www.googleapis.com/auth/drive"]), cache_discovery=False)

out = {}
for code, name in [("CS61A", "cs61a snapshots"),
                   ("CS10",  "cs10 snapshots"),
                   ("CS61C", "cs61c snapshots")]:
    q = (f"name = '{name}' and '{PARENT}' in parents and trashed = false "
         "and mimeType = 'application/vnd.google-apps.folder'")
    found = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    if found:
        fid = found[0]["id"]
    else:
        fid = svc.files().create(
            body={"name": name, "parents": [PARENT],
                  "mimeType": "application/vnd.google-apps.folder"},
            fields="id").execute()["id"]
    out[code] = fid
    print(f"{code}\t{name}\t{fid}")
print(json.dumps(out))
```

- [ ] **Step 2: Run it and record the IDs**

Run: `cd /Users/aaryanmehta/PythonProjects/deadline-export && source .venv/bin/activate && python3 <script>`
Expected: three tab-separated lines plus a JSON map. Save that map — later tasks need it.

- [ ] **Step 3: Verify in Drive**

Re-run the script. Expected: identical IDs (proves idempotency, no duplicate folders).

---

### Task 2: Add the new fields to remind's `courses.json`

**Files:**
- Modify: `remind/services/shared/courses.json`

**Interfaces:**
- Consumes: folder IDs from Task 1
- Produces: canonical per-course table copied into both config secrets

Local `remind` sits on `itsgeagle/feat/per-course-config`. Do **not** switch it. Use a worktree on `main`.

- [ ] **Step 1: Make a worktree on current origin/main**

```bash
cd /Users/aaryanmehta/PythonProjects/remind
git fetch origin
git worktree add /tmp/remind-main origin/main -b chore/course-ids-fa26
```

- [ ] **Step 2: Add two fields to each of the three courses**

In `/tmp/remind-main/services/shared/courses.json`, add to each course object alongside its existing `spreadsheet_id` (do not remove or reorder anything else):

```json
"CS61A": { "gradescope_course_id": "1370542", "drive_folder_id": "<CS61A_FOLDER>" },
"CS10":  { "gradescope_course_id": "1371156", "drive_folder_id": "<CS10_FOLDER>" },
"CS61C": { "gradescope_course_id": "1365362", "drive_folder_id": "<CS61C_FOLDER>" }
```

- [ ] **Step 3: Verify it still parses and sheet IDs are unchanged**

```bash
cd /tmp/remind-main && python3 -c "
import json; d=json.load(open('services/shared/courses.json'))['courses']
expect={'CS61A':'1kWZlV__nnG__wgUoBDTyXrZk9LeJmggvofXIFJ7TUv8','CS10':'1ctQJcUUAsYDKEDQdUZJf3-yEyDMcg7IC6CJiHPK7pp8','CS61C':'14Y8cNWI8bEGaf8l8a_eEliM8ioiqSWoH4UZr0fL-H3o'}
for k,v in expect.items():
    assert d[k]['spreadsheet_id']==v, (k, d[k]['spreadsheet_id'])
    assert d[k]['gradescope_course_id'] and d[k]['drive_folder_id'], k
print('courses.json OK')"
```
Expected: `courses.json OK`

- [ ] **Step 4: Commit and push to main**

```bash
cd /tmp/remind-main
git add services/shared/courses.json
git commit -m "chore: add Gradescope course and Drive folder IDs for Fall 2026"
git push origin HEAD:main
```

If the push is rejected (someone pushed meanwhile), `git pull --rebase origin main` and retry.

---

### Task 3: Multi-course config loading in `simple_sync.py`

**Files:**
- Create: `Simple-Sync/tests/test_config.py`
- Modify: `Simple-Sync/simple_sync.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize_courses(cfg) -> list[dict]`, each dict having keys `course_code: str`, `gradescope_course_id: str`, `spreadsheet_id: str`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from simple_sync import normalize_courses


def test_courses_array_shape():
    cfg = {"courses": [
        {"course_code": "CS61A", "gradescope_course_id": "1370542", "spreadsheet_id": "sheetA"},
        {"course_code": "CS10", "gradescope_course_id": "1371156", "spreadsheet_id": "sheetB"},
    ]}
    got = normalize_courses(cfg)
    assert [c["course_code"] for c in got] == ["CS61A", "CS10"]
    assert got[0]["gradescope_course_id"] == "1370542"
    assert got[1]["spreadsheet_id"] == "sheetB"


def test_legacy_flat_shape_becomes_one_course():
    cfg = {"GRADESCOPE_COURSE_ID": "1324284", "SPREADSHEET_ID": "sheetLegacy"}
    got = normalize_courses(cfg)
    assert len(got) == 1
    assert got[0]["gradescope_course_id"] == "1324284"
    assert got[0]["spreadsheet_id"] == "sheetLegacy"
    assert got[0]["course_code"]  # some non-empty label


def test_ids_coerced_to_str():
    cfg = {"courses": [{"course_code": "CS10", "gradescope_course_id": 1371156,
                        "spreadsheet_id": "s"}]}
    assert normalize_courses(cfg)[0]["gradescope_course_id"] == "1371156"


def test_missing_spreadsheet_id_raises():
    cfg = {"courses": [{"course_code": "CS10", "gradescope_course_id": "1371156"}]}
    with pytest.raises(ValueError, match="CS10"):
        normalize_courses(cfg)


def test_empty_config_raises():
    with pytest.raises(ValueError):
        normalize_courses({})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/aaryanmehta/PythonProjects/Simple-Sync && source .venv/bin/activate && python3 -m pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_courses'`

- [ ] **Step 3: Implement `normalize_courses` in `simple_sync.py`**

Add below `load_credentials`:

```python
def normalize_courses(cfg):
    """Return a list of course dicts from either the `courses` array shape or
    the legacy flat single-course shape."""
    raw = cfg.get("courses")
    if raw is None:
        if "GRADESCOPE_COURSE_ID" in cfg and "SPREADSHEET_ID" in cfg:
            raw = [{
                "course_code": cfg.get("COURSE_CODE") or "default",
                "gradescope_course_id": cfg["GRADESCOPE_COURSE_ID"],
                "spreadsheet_id": cfg["SPREADSHEET_ID"],
            }]
        else:
            raise ValueError(
                "config has neither a 'courses' array nor "
                "GRADESCOPE_COURSE_ID/SPREADSHEET_ID"
            )
    if not raw:
        raise ValueError("config 'courses' is empty")

    out = []
    for i, c in enumerate(raw):
        code = str(c.get("course_code") or f"course[{i}]")
        for key in ("gradescope_course_id", "spreadsheet_id"):
            if not c.get(key):
                raise ValueError(f"course {code}: missing '{key}'")
        out.append({
            "course_code": code,
            "gradescope_course_id": str(c["gradescope_course_id"]),
            "spreadsheet_id": str(c["spreadsheet_id"]),
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py simple_sync.py
git commit -m "feat: parse multi-course sync config with legacy fallback"
```

---

### Task 4: Per-course loop and error isolation in `simple_sync.py`

**Files:**
- Modify: `Simple-Sync/simple_sync.py`
- Modify: `Simple-Sync/tests/test_config.py`

**Interfaces:**
- Consumes: `normalize_courses()` from Task 3
- Produces: `sync_course(gs, course, creds, sleep)`; `run_courses(gs, courses, creds, sleep) -> list[str]` returning failed course codes

- [ ] **Step 1: Write the failing isolation tests**

Append to `tests/test_config.py`:

```python
from simple_sync import run_courses


def test_one_course_failing_does_not_stop_the_others(monkeypatch):
    seen = []

    def fake_sync(gs, course, creds, sleep):
        seen.append(course["course_code"])
        if course["course_code"] == "CS10":
            raise RuntimeError("gradescope exploded")

    import simple_sync
    monkeypatch.setattr(simple_sync, "sync_course", fake_sync)
    courses = [{"course_code": c, "gradescope_course_id": "1", "spreadsheet_id": "s"}
               for c in ("CS61A", "CS10", "CS61C")]
    failed = run_courses(None, courses, {}, 0)
    assert seen == ["CS61A", "CS10", "CS61C"]   # all three attempted
    assert failed == ["CS10"]


def test_all_courses_succeeding_reports_no_failures(monkeypatch):
    import simple_sync
    monkeypatch.setattr(simple_sync, "sync_course", lambda *a, **k: None)
    courses = [{"course_code": "CS61A", "gradescope_course_id": "1", "spreadsheet_id": "s"}]
    assert run_courses(None, courses, {}, 0) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL — `cannot import name 'run_courses'`

- [ ] **Step 3: Refactor `main()` into `sync_course` + `run_courses`**

Replace everything in `main()` from `log.info("syncing roster...")` through the assignment loop with a `sync_course` function, and add `run_courses`:

```python
def sync_course(gs, course, creds, sleep):
    """Sync one course's roster + every assignment into its spreadsheet."""
    course_id = course["gradescope_course_id"]
    log.info("=== %s: course=%s spreadsheet=%s",
             course["course_code"], course_id, course["spreadsheet_id"])
    spreadsheet = open_sheets(course["spreadsheet_id"], creds["service_account"])

    log.info("  syncing roster...")
    roster_rows = gs.fetch_roster(course_id)
    if roster_rows:
        try:
            ws = spreadsheet.worksheet("Roster")
            with_retry(ws.clear)
        except gspread.WorksheetNotFound:
            ws = with_retry(spreadsheet.add_worksheet, title="Roster",
                            rows=max(len(roster_rows), 100), cols=4)
        with_retry(ws.update, range_name="A1", values=roster_rows, value_input_option="RAW")
        log.info("    roster: %d students", len(roster_rows) - 1)
    else:
        log.warning("    roster: empty or inaccessible")
    time.sleep(sleep)

    assignments = gs.fetch_assignments(course_id)
    log.info("  found %d assignments", len(assignments))
    for aid, title in assignments:
        tab = safe_tab_name(title)
        log.info("    [%s] %s", aid, tab)
        try:
            csv_text = gs.download_scores(course_id, aid)
        except Exception as e:
            log.error("      download failed: %s", e)
            continue
        write_csv_to_tab(spreadsheet, tab, csv_text)
        time.sleep(sleep)


def run_courses(gs, courses, creds, sleep):
    """Sync every course. One failure never aborts the rest.
    Returns the list of failed course codes."""
    failed = []
    for course in courses:
        try:
            sync_course(gs, course, creds, sleep)
        except Exception as e:
            log.exception("  %s FAILED: %s", course["course_code"], e)
            failed.append(course["course_code"])
    return failed
```

`run_courses` must call the module-level `sync_course` by global lookup (a plain
call, not a captured reference) so `monkeypatch.setattr` works.

- [ ] **Step 4: Rewrite `main()` to use them, with `--only` and `--dry-run`**

```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=CLASS_JSON_DEFAULT)
    parser.add_argument("--credentials", default=CREDS_JSON_DEFAULT)
    parser.add_argument("--sleep", type=float, default=1.2,
                        help="seconds between Sheets writes (default 1.2; ~50/min)")
    parser.add_argument("--only", default=None,
                        help="sync only this course_code")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the courses that would sync, then exit")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_json(resolve(args.config, here, subdir="config"))
    creds = load_credentials(resolve(args.credentials, here))

    courses = normalize_courses(cfg)
    if args.only:
        courses = [c for c in courses if c["course_code"] == args.only]
        if not courses:
            raise SystemExit(f"no course with course_code={args.only!r} in config")

    log.info("syncing %d course(s): %s",
             len(courses), ", ".join(c["course_code"] for c in courses))
    if args.dry_run:
        for c in courses:
            log.info("  would sync %s: course=%s spreadsheet=%s",
                     c["course_code"], c["gradescope_course_id"], c["spreadsheet_id"])
        return

    gs = GSSession()
    gs.log_in(creds["gradescope_email"], creds["gradescope_password"])
    log.info("Gradescope login ok")

    failed = run_courses(gs, courses, creds, args.sleep)
    if failed:
        log.error("done with failures: %s", ", ".join(failed))
        sys.exit(1)
    log.info("done — %d course(s) synced", len(courses))
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add tests/test_config.py simple_sync.py
git commit -m "feat: sync all configured courses in one run with per-course isolation"
```

---

### Task 5: `Simple-Sync` config, docs and deploy settings

**Files:**
- Modify: `Simple-Sync/config/example.json`
- Modify: `Simple-Sync/deploy.sh`
- Modify: `Simple-Sync/README.md`
- Create: `Simple-Sync/config/custom_sync.json` (gitignored — local only)

**Interfaces:**
- Consumes: course table from Global Constraints
- Produces: the config file uploaded as secret `simple-sync-config`

- [ ] **Step 1: Write the real local config** (`config/custom_sync.json`, gitignored)

```json
{
  "courses": [
    {"course_code": "CS61A", "gradescope_course_id": "1370542",
     "spreadsheet_id": "1kWZlV__nnG__wgUoBDTyXrZk9LeJmggvofXIFJ7TUv8"},
    {"course_code": "CS10", "gradescope_course_id": "1371156",
     "spreadsheet_id": "1ctQJcUUAsYDKEDQdUZJf3-yEyDMcg7IC6CJiHPK7pp8"},
    {"course_code": "CS61C", "gradescope_course_id": "1365362",
     "spreadsheet_id": "14Y8cNWI8bEGaf8l8a_eEliM8ioiqSWoH4UZr0fL-H3o"}
  ]
}
```

- [ ] **Step 2: Mirror the shape into `config/example.json`** (committed) using the same three courses but placeholder IDs (`"1234567"`, `"1AbC...your-sheet-id..."`).

- [ ] **Step 3: Verify the config parses and is dry-runnable**

Run: `python3 simple_sync.py --dry-run`
Expected: logs `syncing 3 course(s): CS61A, CS10, CS61C` then one `would sync` line each, exit 0.

- [ ] **Step 4: Raise the task timeout in `deploy.sh`**

Change `--task-timeout="30m"` to `--task-timeout="60m"` (CS10 alone is 101 assignments; the run grows through the term).

- [ ] **Step 5: Update `README.md`** — replace the single-course config example with the `courses` array, and document `--only` and `--dry-run`.

- [ ] **Step 6: Commit**

```bash
git add config/example.json deploy.sh README.md
git commit -m "feat: multi-course config, 60m task timeout, docs"
```

---

### Task 6: Multi-course config and `--date` in `deadline_export.py`

**Files:**
- Modify: `deadline-export/deadline_export.py`
- Modify: `deadline-export/tests/test_logic.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (separate repo)
- Produces: `normalize_courses(cfg) -> list[dict]` with keys `course_code`, `gradescope_course_id`, `spreadsheet_id`, `drive_folder_id`; `target_day(tz, date_str)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_logic.py`:

```python
import datetime
import pytest
from deadline_export import normalize_courses, target_day


def test_courses_array_shape():
    cfg = {"courses": [{"course_code": "CS61A", "gradescope_course_id": "1370542",
                        "spreadsheet_id": "s", "drive_folder_id": "f"}]}
    got = normalize_courses(cfg)
    assert got[0]["course_code"] == "CS61A"
    assert got[0]["drive_folder_id"] == "f"


def test_legacy_flat_shape():
    cfg = {"COURSE_CODE": "CS61A", "GRADESCOPE_COURSE_ID": "1324284",
           "SPREADSHEET_ID": "s", "DRIVE_FOLDER_ID": "f"}
    got = normalize_courses(cfg)
    assert len(got) == 1 and got[0]["gradescope_course_id"] == "1324284"


def test_missing_drive_folder_raises():
    cfg = {"courses": [{"course_code": "CS10", "gradescope_course_id": "1",
                        "spreadsheet_id": "s"}]}
    with pytest.raises(ValueError, match="CS10"):
        normalize_courses(cfg)


def test_target_day_explicit_date_wins():
    assert target_day("America/Los_Angeles", "2026-09-08") == datetime.date(2026, 9, 8)


def test_target_day_defaults_to_yesterday():
    got = target_day("America/Los_Angeles", None)
    assert isinstance(got, datetime.date)
    assert got < datetime.datetime.now().date() + datetime.timedelta(days=1)


def test_target_day_rejects_garbage():
    with pytest.raises(ValueError):
        target_day("America/Los_Angeles", "not-a-date")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/aaryanmehta/PythonProjects/deadline-export && source .venv/bin/activate && python3 -m pytest tests/ -v`
Expected: FAIL — `cannot import name 'normalize_courses'`

- [ ] **Step 3: Implement both helpers**

Add after `load_credentials`:

```python
def normalize_courses(cfg):
    """Return course dicts from either the `courses` array or the legacy flat shape."""
    raw = cfg.get("courses")
    if raw is None:
        if "GRADESCOPE_COURSE_ID" in cfg and "SPREADSHEET_ID" in cfg:
            raw = [{
                "course_code": cfg.get("COURSE_CODE") or "default",
                "gradescope_course_id": cfg["GRADESCOPE_COURSE_ID"],
                "spreadsheet_id": cfg["SPREADSHEET_ID"],
                "drive_folder_id": cfg.get("DRIVE_FOLDER_ID"),
            }]
        else:
            raise ValueError(
                "config has neither a 'courses' array nor "
                "GRADESCOPE_COURSE_ID/SPREADSHEET_ID"
            )
    if not raw:
        raise ValueError("config 'courses' is empty")

    out = []
    for i, c in enumerate(raw):
        code = str(c.get("course_code") or f"course[{i}]")
        for key in ("gradescope_course_id", "spreadsheet_id", "drive_folder_id"):
            if not c.get(key):
                raise ValueError(f"course {code}: missing '{key}'")
        out.append({
            "course_code": code,
            "gradescope_course_id": str(c["gradescope_course_id"]),
            "spreadsheet_id": str(c["spreadsheet_id"]),
            "drive_folder_id": str(c["drive_folder_id"]),
        })
    return out


def target_day(tz_name, date_str=None):
    """The calendar day to export: an explicit YYYY-MM-DD, else yesterday in tz."""
    if date_str:
        return datetime.date.fromisoformat(date_str)
    return pacific_yesterday(tz_name)
```

`datetime.date.fromisoformat` already raises `ValueError` on garbage, satisfying the last test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all pass (6 new + the existing ones)

- [ ] **Step 5: Commit**

```bash
git add tests/test_logic.py deadline_export.py
git commit -m "feat: parse multi-course export config and add --date"
```

---

### Task 7: Per-course export loop in `deadline_export.py`

**Files:**
- Modify: `deadline-export/deadline_export.py`

**Interfaces:**
- Consumes: `normalize_courses()`, `target_day()` from Task 6
- Produces: `export_course(gs, drive, course, creds, day, sleep) -> int` (count exported)

This is the task that fixes the cross-term bug: the Gradescope course ID becomes per-course and is selected by the same `course_code` that filters the Firestore deadlines.

- [ ] **Step 1: Extract `export_course` from `main()`**

```python
def export_course(gs, drive, course, creds, day, sleep):
    """Export every assignment in `course` whose deadline fell on `day`.
    Returns the number of assignments uploaded to Drive."""
    code = course["course_code"]
    due_names = fetch_due_assignment_names(
        creds["firebase_service_account"], code, day)
    if not due_names:
        log.info("  %s: nothing due on %s", code, day)
        return 0
    log.info("  %s: due on %s: %s", code, day, sorted(due_names))

    spreadsheet = open_sheets(course["spreadsheet_id"], creds["sheets_service_account"])
    course_id = course["gradescope_course_id"]
    assignments = gs.fetch_assignments(course_id)
    by_title = {t: aid for aid, t in assignments}
    by_safe = {safe_tab_name(t): (aid, t) for aid, t in assignments}

    exported = 0
    for name in sorted(due_names):
        aid, title = by_title.get(name), name
        if aid is None and safe_tab_name(name) in by_safe:
            aid, title = by_safe[safe_tab_name(name)]
        if aid is None:
            log.warning("    %s: no Gradescope assignment matches '%s' — skipping", code, name)
            continue
        try:
            csv_text = gs.download_scores(course_id, aid)
        except Exception as e:
            log.error("    [%s] download failed: %s", title, e)
            continue
        tab = safe_tab_name(title)
        try:
            write_csv_to_tab(spreadsheet, tab, csv_text)
            log.info("    [%s] sheet tab updated", tab)
        except Exception as e:
            log.error("    [%s] sheet write failed: %s", tab, e)
        try:
            fname = drive_filename(title, day)
            fid = drive.upsert_csv(course["drive_folder_id"], fname, csv_text)
            log.info("    [%s] uploaded to Drive as '%s' (%s)", tab, fname, fid)
            exported += 1
        except Exception as e:
            log.error("    [%s] drive upload failed: %s", tab, e)
        time.sleep(sleep)
    return exported
```

- [ ] **Step 2: Rewrite `main()` to loop with isolation**

```python
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="custom_export.json")
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument("--sleep", type=float, default=1.2)
    parser.add_argument("--date", default=None,
                        help="export deadlines due on this YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--only", default=None, help="only this course_code")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_json(resolve(args.config, here, subdir="config"))
    creds = load_credentials(resolve(args.credentials, here))

    tz = cfg.get("TIMEZONE", "America/Los_Angeles")
    day = target_day(tz, args.date)
    courses = normalize_courses(cfg)
    if args.only:
        courses = [c for c in courses if c["course_code"] == args.only]
        if not courses:
            raise SystemExit(f"no course with course_code={args.only!r} in config")

    log.info("checking deadlines due on %s (%s) for %s",
             day, tz, ", ".join(c["course_code"] for c in courses))

    gs = GSSession()
    gs.log_in(creds["gradescope_email"], creds["gradescope_password"])
    log.info("Gradescope login ok")

    oauth_user = creds.get("drive_oauth") or None
    impersonate = cfg.get("DRIVE_IMPERSONATE_USER") or None
    if oauth_user:
        log.info("Drive uploads via OAuth user (owns files, no Shared Drive needed)")
    elif impersonate:
        log.info("Drive uploads impersonating %s (domain-wide delegation)", impersonate)
    drive = DriveUploader(creds["sheets_service_account"],
                          impersonate=impersonate, oauth_user_info=oauth_user)

    total, failed = 0, []
    for course in courses:
        try:
            total += export_course(gs, drive, course, creds, day, args.sleep)
        except Exception as e:
            log.exception("  %s FAILED: %s", course["course_code"], e)
            failed.append(course["course_code"])

    if failed:
        log.error("done with failures: %s", ", ".join(failed))
        sys.exit(1)
    log.info("done — exported %d assignment(s) across %d course(s)", total, len(courses))
```

- [ ] **Step 3: Run the suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass (no regressions — these are pure-logic tests)

- [ ] **Step 4: Commit**

```bash
git add deadline_export.py
git commit -m "feat: export every configured course, binding course ID to course code"
```

---

### Task 8: `deadline-export` config and docs

**Files:**
- Modify: `deadline-export/config/example.json`
- Modify: `deadline-export/README.md`
- Create: `deadline-export/config/custom_export.json` (gitignored)

- [ ] **Step 1: Write the real local config**

```json
{
  "TIMEZONE": "America/Los_Angeles",
  "DRIVE_IMPERSONATE_USER": "",
  "courses": [
    {"course_code": "CS61A", "gradescope_course_id": "1370542",
     "spreadsheet_id": "1kWZlV__nnG__wgUoBDTyXrZk9LeJmggvofXIFJ7TUv8",
     "drive_folder_id": "<CS61A_FOLDER>"},
    {"course_code": "CS10", "gradescope_course_id": "1371156",
     "spreadsheet_id": "1ctQJcUUAsYDKEDQdUZJf3-yEyDMcg7IC6CJiHPK7pp8",
     "drive_folder_id": "<CS10_FOLDER>"},
    {"course_code": "CS61C", "gradescope_course_id": "1365362",
     "spreadsheet_id": "14Y8cNWI8bEGaf8l8a_eEliM8ioiqSWoH4UZr0fL-H3o",
     "drive_folder_id": "<CS61C_FOLDER>"}
  ]
}
```

- [ ] **Step 2: Mirror the shape into `config/example.json`** with placeholder IDs.

- [ ] **Step 3: Update `README.md`** — the config block, the `--date` and `--only` flags, and a note that each course's Gradescope ID must match the term its Firestore deadlines describe (this is the bug that produced the September snapshots).

- [ ] **Step 4: Verify config parses**

```bash
python3 -c "
import json,sys; sys.path.insert(0,'.')
from deadline_export import normalize_courses
cs=normalize_courses(json.load(open('config/custom_export.json')))
print(len(cs),'courses:',[c['course_code'] for c in cs])"
```
Expected: `3 courses: ['CS61A', 'CS10', 'CS61C']`

- [ ] **Step 5: Commit**

```bash
git add config/example.json README.md
git commit -m "docs: multi-course export config and term-matching caveat"
```

---

### Task 9: Deploy both jobs

**Files:**
- Uses: `Simple-Sync/deploy.sh`, `deadline-export/deploy.sh`

- [ ] **Step 1: Push the new config secrets**

```bash
cd /Users/aaryanmehta/PythonProjects/Simple-Sync
gcloud secrets versions add simple-sync-config \
  --data-file="config/custom_sync.json" --project=autoremind-480200
cd /Users/aaryanmehta/PythonProjects/deadline-export
gcloud secrets versions add deadline-export-config \
  --data-file="config/custom_export.json" --project=autoremind-480200
```

- [ ] **Step 2: Build and deploy both**

```bash
cd /Users/aaryanmehta/PythonProjects/Simple-Sync && bash deploy.sh
cd /Users/aaryanmehta/PythonProjects/deadline-export && bash deploy.sh
```
Expected: both end with the "Done" banner. Both scripts are idempotent.

- [ ] **Step 3: Smoke-test the sync on the smallest course first**

```bash
gcloud run jobs execute simple-sync --region=us-central1 \
  --project=autoremind-480200 --args="--only,CS61C" --wait
```
Expected: exit 0. CS61C has 4 assignments, so this is the cheapest real check.

- [ ] **Step 4: Full sync run**

```bash
gcloud run jobs execute simple-sync --region=us-central1 --project=autoremind-480200 --wait
gcloud logging read \
  'resource.type=cloud_run_job AND resource.labels.job_name=simple-sync' \
  --project=autoremind-480200 --limit=40 --format='value(textPayload)' --freshness=1h
```
Expected: `syncing 3 course(s): CS61A, CS10, CS61C`, three `===` course banners, `done — 3 course(s) synced`, no `FAILED`.

- [ ] **Step 5: Verify all three sheets actually got fresh data**

Confirm each spreadsheet's `Roster` tab is non-empty and the tab count matches the Gradescope assignment count (CS61A 8, CS10 101, CS61C 4, plus `Roster`). CS61A's roster should read ~1621 students — if it reads ~198, it is still pointed at Summer.

---

### Task 10: Retire `gradesync-daily-job` and re-export the bad snapshots

**Files:** none (infrastructure + data repair)

- [ ] **Step 1: Delete the broken scheduler and job**

```bash
gcloud scheduler jobs delete gradesync-daily --location=us-central1 \
  --project=autoremind-480200 --quiet
gcloud run jobs delete gradesync-daily-job --region=us-central1 \
  --project=autoremind-480200 --quiet
```

- [ ] **Step 2: Confirm what remains**

```bash
gcloud run jobs list --project=autoremind-480200
gcloud scheduler jobs list --location=us-central1 --project=autoremind-480200
```
Expected: exactly `simple-sync`, `deadline-export`, `autoremind-daily-job` and their three schedulers.

- [ ] **Step 3: Re-export the four Summer-contaminated snapshots**

Only after Task 9 confirms CS61A resolves to Fall. Run locally, one date at a time:

```bash
cd /Users/aaryanmehta/PythonProjects/deadline-export && source .venv/bin/activate
for d in 2026-09-01 2026-09-03 2026-09-08; do
  python3 deadline_export.py --only CS61A --date "$d"
done
```
`Lab 0` and `Lab 1` share 2026-09-01, so three runs cover all four files.

- [ ] **Step 4: Verify the repaired snapshots hold Fall students**

Re-run the verification from the spec against the new `cs61a snapshots` folder: overlap with the **Fall** roster (1621) should now dominate, and overlap with Summer (198) should collapse.

- [ ] **Step 5: Commit the plan's completion notes**

```bash
cd /Users/aaryanmehta/PythonProjects/Simple-Sync
git add docs/superpowers/plans/2026-09-10-multi-course-sync.md
git commit -m "docs: mark multi-course sync plan complete"
```

---

## Notes for the executor

- The old files in `cs61a autoremind pilot data` (`1L7Lq…`) are genuine Summer pilot history — **leave them alone**. Only the four September files are wrong, and they are re-exported into the new `cs61a snapshots` folder, not the old one.
- If `--only CS61C` fails with a Sheets 403, the sheet was un-shared from `gradesync@`; all three were verified shared on 2026-09-10.
- Summer sheet `1HPa…` stops updating the moment Task 9 lands. That is intended (spec decision: freeze).
