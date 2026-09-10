#!/usr/bin/env python3
"""
Lightweight Gradescope -> Google Sheets sync.

For the configured Gradescope course, pulls every assignment's scores CSV
and writes it to a Google Sheets tab named after the assignment. CSV is
written as-is (no formulas, no gradebook aggregation).

Usage:
    python3 simple_sync.py
    python3 simple_sync.py --config my_class.json --credentials creds.json

Credentials: a single JSON file (default: ./credentials.json, gitignored):

    {
      "gradescope_email": "...",
      "gradescope_password": "...",
      "service_account": { ...full GCP service-account key json object... }
    }

Config (default: ./config/custom_sync.json):

    {
      "GRADESCOPE_COURSE_ID": "1234567",
      "SPREADSHEET_ID": "1AbC..."
    }
"""

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import time

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials


CLASS_JSON_DEFAULT = "custom_sync.json"
CREDS_JSON_DEFAULT = "credentials.json"
GS_BASE = "https://www.gradescope.com"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("simple_sync")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_credentials(path):
    data = load_json(path)
    missing = [k for k in ("gradescope_email", "gradescope_password", "service_account") if k not in data]
    if missing:
        raise RuntimeError(f"credentials file missing keys: {missing}")
    return data


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


class GSSession:
    """Minimal Gradescope client: logged-in requests.Session + helpers."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 SimpleSync"})

    def log_in(self, email, password):
        r = self.session.get(f"{GS_BASE}/login")
        r.raise_for_status()
        token_el = BeautifulSoup(r.text, "html.parser").find(
            "input", attrs={"name": "authenticity_token"}
        )
        if not token_el:
            raise RuntimeError("Could not find authenticity_token on /login")
        payload = {
            "utf8": "✓",
            "authenticity_token": token_el["value"],
            "session[email]": email,
            "session[password]": password,
            "session[remember_me]": "0",
            "commit": "Log In",
            "session[remember_me_code]": "",
        }
        self.session.post(f"{GS_BASE}/login", data=payload, allow_redirects=True).raise_for_status()
        check = self.session.get(f"{GS_BASE}/account")
        if check.status_code != 200:
            raise RuntimeError(f"Login failed (status {check.status_code}); check email/password")

    def download_scores(self, course_id, assignment_id):
        url = f"{GS_BASE}/courses/{course_id}/assignments/{assignment_id}/scores.csv"
        r = self.session.get(url)
        r.raise_for_status()
        return r.text

    def fetch_roster(self, course_id):
        """Returns list of [Name, SID, Email, Role] rows (header first)."""
        r = self.session.get(f"{GS_BASE}/courses/{course_id}/memberships.csv")
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        if not rows:
            return []
        header = [h.strip() for h in rows[0]]
        def col(*names):
            for name in names:
                try:
                    return header.index(name)
                except ValueError:
                    pass
            return None
        i_first = col("First Name", "first_name", "first name")
        i_last  = col("Last Name", "last_name", "last name")
        i_name  = col("Name", "name")
        i_sid   = col("SID", "Student ID", "student_id", "ID")
        i_email = col("Email", "email")
        i_role  = col("Role", "role")
        out = [["Name", "SID", "Email", "Role"]]
        for row in rows[1:]:
            def get(i):
                return row[i].strip() if i is not None and i < len(row) else ""
            if i_name is not None:
                name = get(i_name)
            else:
                name = f"{get(i_first)} {get(i_last)}".strip()
            out.append([name, get(i_sid), get(i_email), get(i_role)])
        return out

    def fetch_assignments(self, course_id):
        """Returns list of (assignment_id, title) parsed from the assignments page."""
        r = self.session.get(f"{GS_BASE}/courses/{course_id}/assignments")
        r.raise_for_status()
        body = r.text.replace("\\u0026", "&")
        seen, out = set(), []
        for aid, title in re.findall(r'\{"id":(\d+),"title":"([^"}]+)"', body):
            if aid in seen:
                continue
            seen.add(aid)
            out.append((aid, title))
        return out


def open_sheets(spreadsheet_id, service_account_info):
    creds = Credentials.from_service_account_info(service_account_info, scopes=[SHEETS_SCOPE])
    return gspread.authorize(creds).open_by_key(spreadsheet_id)


def safe_tab_name(name):
    # Google Sheets tab names: <=100 chars, no [ ] * ? : / \
    cleaned = re.sub(r"[\[\]\*\?:/\\]", "-", name).strip()
    return cleaned[:100] or "untitled"


def with_retry(fn, *args, **kwargs):
    delay = 5
    for attempt in range(6):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = getattr(e.response, "status_code", None)
            if status not in (429, 500, 503):
                raise
            log.warning("  sheets %s, backing off %ss (attempt %d)", status, delay, attempt + 1)
            time.sleep(delay)
            delay = min(delay * 2, 70)
    raise RuntimeError("sheets API: too many retries")


def write_csv_to_tab(spreadsheet, tab_name, csv_text):
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        log.info("  empty CSV, skipping %s", tab_name)
        return
    try:
        ws = spreadsheet.worksheet(tab_name)
        with_retry(ws.clear)
    except gspread.WorksheetNotFound:
        cols = max(len(r) for r in rows)
        ws = with_retry(
            spreadsheet.add_worksheet,
            title=tab_name,
            rows=max(len(rows), 100),
            cols=max(cols, 10),
        )
    with_retry(ws.update, range_name="A1", values=rows, value_input_option="RAW")


def resolve(path, here, subdir=None):
    if os.path.isabs(path):
        return path
    if subdir and not os.path.dirname(path):
        return os.path.join(here, subdir, path)
    return os.path.join(here, path)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=CLASS_JSON_DEFAULT)
    parser.add_argument("--credentials", default=CREDS_JSON_DEFAULT)
    parser.add_argument(
        "--sleep", type=float, default=1.2,
        help="seconds between Sheets writes (default 1.2; ~50/min)",
    )
    parser.add_argument("--only", default=None, help="sync only this course_code")
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


if __name__ == "__main__":
    main()
