# Multi-course Gradescope sync — design

**Date:** 2026-09-10
**Status:** approved, ready for implementation planning

## Problem

Three Fall 2026 courses (CS 61A, CS 10, CS 61C) need a daily Gradescope → Google
Sheets sync and a nightly due-assignment CSV snapshot. Today only CS 61A is
covered, and it is pointed at the wrong term.

Investigation found the deployed state is worse than "one course is missing":

| Job | Schedule | Points at | Status |
|---|---|---|---|
| `simple-sync` | 6:00 PT | CS61A **Summer** `1324284` → `1HPa…` | works, stale term |
| `gradesync-daily-job` | 01:00 PT | CS61A **Spring** `1297200` → `1tDmN…` | crashes every run |
| `deadline-export` | 00:05 PT | CS61A **Summer** `1324284` + **Fall** deadlines | runs, wrong data |
| `autoremind-daily-job` | 09:00 PT | reminders | healthy, out of scope |

### Three defects

1. **`gradesync-daily-job` has never worked.** remind's `simple_sync.py` detects
   Cloud Run with `os.getenv("K_SERVICE") or os.getenv("K_JOB")`. Cloud Run
   *Jobs* set `CLOUD_RUN_JOB`, not `K_JOB`, so it takes the local-file branch and
   dies on `FileNotFoundError: /app/config/sync_config.json`. Failing since June.

2. **`deadline-export` mixes terms.** It reads Fall 2026 deadlines from Firestore
   but downloads scores from the Summer course, because the Gradescope course ID
   is global while `course_code` is per-deadline. Verified against the Sep 8
   snapshot:

   ```
   'Lab 2 - 2026-09-08.csv': 213 students
     vs CS61A SUMMER roster= 198  overlap=198  (93.0%)
     vs CS61A FALL   roster=1621  overlap= 18  ( 8.5%)
   ```

   Four files in `cs61a autoremind pilot data` are affected: `Lab 0 - 2026-09-01`,
   `Lab 1 - 2026-09-01`, `Homework 1 - 2026-09-03`, `Lab 2 - 2026-09-08`.

3. **Two diverging copies of `simple_sync.py`** — this repo's and
   `remind/gradesync_input/`. Only this one works.

## Goals

- One daily job syncing all three Fall 2026 courses.
- Nightly due-assignment snapshots per course, into per-course Drive folders.
- One place to declare a course.
- Retire the broken duplicate.

**Non-goals:** touching `autoremind-daily-job`; the Sheets → Supabase path
(`gradesync_to_db.py`, already multi-course); backfilling Summer history.

## Decisions

| Decision | Choice |
|---|---|
| Sync owner | `Simple-Sync`, one multi-course job |
| Summer CS61A | Freeze — stop syncing, keep sheet `1HPa…` intact |
| Config source of truth | remind's `services/shared/courses.json`, copied into the other repos |
| Drive layout | Subfolders of `[fa26] autoremind sheets` (`1UT3Dl…`) |
| `courses.json` change | Directly on `main` (additive only) |

## Verified facts

Course IDs resolved against Gradescope as `autoremindberkeley@gmail.com`:

| ID | Course | Assignments |
|---|---|---|
| `1370542` | CS 61A Fall 2026 | 8 (roster 1621) |
| `1371156` | CS10 Fall 2026 | 101 |
| `1365362` | CS 61C Fall 2026 | 4 |
| `1324284` | CS 61A Summer 2026 | 32 (roster 198) — freeze |
| `1297200` | CS 61A Duplicate, Spring 2026 | — retire |

- All three Fall sheets are already shared with
  `gradesync@autoremind-480200.iam.gserviceaccount.com` and already populated
  (CS10 has 102 tabs) from a manual run.
- Firestore `deadlines` already holds Fall 2026 rows: CS61A 47, CS61C 24, CS10 17.
  No deadline data work needed.
- Drive folder `1UT3Dl…` (`[fa26] autoremind sheets`) is writable by
  `autoremind@berkeley.edu`, the Drive OAuth identity. It holds the three sheets.

## Design

### Config schema

`courses.json` gains two additive fields per course:

```json
"CS61A": {
  "display_name": "CS 61A",
  "spreadsheet_id": "1kWZlV__nnG__wgUoBDTyXrZk9LeJmggvofXIFJ7TUv8",
  "gradescope_course_id": "1370542",
  "drive_folder_id": "<created during implementation>"
}
```

Both consumers ship a copy as their Secret Manager config, normalised to a
`courses` array. **Loaders must also accept the current flat shape**
(`GRADESCOPE_COURSE_ID` + `SPREADSHEET_ID` = a single course) so the existing
secret keeps working and the rollout cannot hard-break.

### `simple_sync.py`

`main()` splits into `sync_course(gs, course_cfg, creds, sleep)` plus a loop over
courses. One Gradescope login is reused across all courses.

Each course runs inside try/except: a failure logs and moves on to the next
course, and the job exits non-zero at the end if any course failed. One broken
course must not silently cost the other two, but it must still alert.

Courses run **serially** on the existing 1.2s write spacing. This is the reason
for a single job rather than three: three concurrent jobs would triple the
request rate into the same 60-writes/min service-account quota. Estimated full
run ≈ 8 minutes for 113 assignments. Raise `--task-timeout` to 60m for headroom
as the semester grows.

### `deadline_export.py`

Same loop shape. The Gradescope course ID, spreadsheet, and Drive folder all
become per-course and are selected by the same `course_code` used to filter
Firestore deadlines — which is what fixes defect 2 structurally, rather than by
correcting one ID.

Adds `--date YYYY-MM-DD` (default: yesterday in `TIMEZONE`) so the four bad
September files can be re-exported.

### Drive folders

Create `cs61a`, `cs10`, `cs61c` inside `1UT3Dl…`; record IDs in the configs.
Existing `cs61a autoremind pilot data` (`1L7Lq…`) is left in place — it holds
genuine Summer pilot history.

## Cutover

1. Create the three Drive subfolders.
2. Land `courses.json` additions on remind `main`.
3. Implement + test both loops.
4. Push new config secrets; deploy both images.
5. Manual `--dry-run`, then a real run of each job, verified in logs.
6. Delete `gradesync-daily-job` and the `gradesync-daily` scheduler.
7. Re-export the four September snapshots with `--date`, overwriting them.

Cutover is complete when a scheduled `simple-sync` run writes all three Fall
sheets, and `deadline-export` picks the right course for a due assignment.

## Testing

TDD on pure logic, which is where the real bugs live:

- config normalisation: new `courses` array, legacy flat shape, missing fields
- per-course error isolation: one course raising does not abort the others, and
  the run still exits non-zero
- date selection for `--date` vs default-yesterday
- existing tab-name / filename sanitising

`deadline-export/tests/` already exists; add `tests/` to `Simple-Sync`. Network
paths (Gradescope, Sheets, Drive, Firestore) stay manual via `--dry-run`.

## Risks

| Risk | Mitigation |
|---|---|
| CS10's 101 assignments approach the Sheets write quota | Serial execution, 1.2s spacing, existing 429 backoff |
| Bad config takes down all three courses at once | Per-course isolation; legacy shape still accepted |
| Drive OAuth token expiry breaks uploads | Pre-existing; `setup_drive_oauth.py` re-mints. Out of scope |
| Summer sheet consumers surprised when it stops updating | Sheet is frozen, not deleted — flag to the team |

## Open items

- Whoever watches sheet `1HPa…` should be told it stops updating.
- The four corrupt September snapshots are Summer data. Plan is to overwrite via
  `--date`; if the study wants them preserved as-is, say so before step 7.
