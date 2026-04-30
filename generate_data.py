"""
JSX Compliance Data Relay
=========================
Reads the Planes and Debriefs tables from JSX Master Sheet.xlsx on SharePoint
and writes data.json to the repo root for the GitHub Pages dashboard to consume.

Runs on GitHub Actions on an hourly schedule.

Credentials required (set as GitHub Secrets):
  TENANT_ID       - Azure AD tenant ID
  CLIENT_ID       - Foxtrot Report Automation app ID
  CLIENT_SECRET   - Foxtrot Report Automation client secret

File locations:
  SharePoint Drive ID : b!_bzXaIx86kOufgJN3ih-BaDIDthKYuxJkJtLi1Bm5irGjCEnK-VHSpBRRm3_SDKU
  File path           : Power Flows/JSX/JSX Master Sheet.xlsx
  Sheets read         : Planes, Debriefs
"""

import os
import json
import sys
import requests
from datetime import datetime, timezone, date

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

TENANT_ID     = os.environ["TENANT_ID"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]

DRIVE_ID  = "b!_bzXaIx86kOufgJN3ih-BaDIDthKYuxJkJtLi1Bm5irGjCEnK-VHSpBRRm3_SDKU"
FILE_PATH = "Power Flows/JSX/JSX Master Sheet.xlsx"

# Job cycle lengths in days
CYCLES = {"IC": 30, "EC": 90, "DSC": 30, "CE": 90}

# ─────────────────────────────────────────────
# STEP 1: Get Graph API token
# ─────────────────────────────────────────────

def get_token():
    print("Acquiring Graph API token...")
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    resp = requests.post(url, data={
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    })
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("  Token acquired.")
    return token


# ─────────────────────────────────────────────
# STEP 2: Download Excel file from SharePoint
# ─────────────────────────────────────────────

def download_excel(token):
    print(f"Downloading: {FILE_PATH}")
    encoded = FILE_PATH.replace(" ", "%20")
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}"
        f"/root:/{encoded}:/content"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    path = "/tmp/jsx_master_sheet.xlsx"
    with open(path, "wb") as f:
        f.write(resp.content)
    print(f"  Downloaded {len(resp.content):,} bytes → {path}")
    return path


# ─────────────────────────────────────────────
# STEP 3: Parse Excel — Planes sheet
# ─────────────────────────────────────────────

def parse_planes(wb):
    """
    Reads the Planes sheet. Columns expected:
      0: Tail Number
      1: Plane Type
      2: Last Service   (date or empty)
      3: Last IC        (date or empty)
      4: Last EC        (date or empty)
      5: Last DSC       (date or empty)
      6: Last CE        (date or empty)
      7-10: Compliance window columns (formula-driven — we recalculate from dates)

    We recalculate compliance windows here rather than relying on Excel formula
    results, which openpyxl can't evaluate. Formula: cycle - (today - last_date).
    """
    import openpyxl
    ws = wb["Planes"]
    today = date.today()
    planes = []

    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    print(f"  Planes sheet: {len(rows)-1} data rows")

    for row in rows[1:]:
        tail = row[0]
        if not tail:
            continue

        plane_type   = row[1] or ""
        last_service = row[2]  # may be datetime or None

        # Parse each last-service date
        last_dates = {}
        for i, job in enumerate(["IC", "EC", "DSC", "CE"]):
            val = row[3 + i]
            if isinstance(val, datetime):
                last_dates[job] = val.date()
            elif isinstance(val, date):
                last_dates[job] = val
            else:
                last_dates[job] = None

        # Calculate compliance windows
        windows = {}
        for job, cycle in CYCLES.items():
            if last_dates[job] is None:
                windows[job] = "No Service"
            else:
                days_since = (today - last_dates[job]).days
                windows[job] = cycle - days_since

        # Last service date (most recent of any job)
        valid_dates = [d for d in last_dates.values() if d is not None]
        if isinstance(last_service, datetime):
            last_svc_str = last_service.date().isoformat()
        elif isinstance(last_service, date):
            last_svc_str = last_service.isoformat()
        elif valid_dates:
            last_svc_str = max(valid_dates).isoformat()
        else:
            last_svc_str = None

        planes.append({
            "tail":        str(tail),
            "type":        str(plane_type),
            "lastService": last_svc_str,
            "lastIC":      last_dates["IC"].isoformat()  if last_dates["IC"]  else None,
            "lastEC":      last_dates["EC"].isoformat()  if last_dates["EC"]  else None,
            "lastDSC":     last_dates["DSC"].isoformat() if last_dates["DSC"] else None,
            "lastCE":      last_dates["CE"].isoformat()  if last_dates["CE"]  else None,
            "IC":          windows["IC"],
            "EC":          windows["EC"],
            "DSC":         windows["DSC"],
            "CE":          windows["CE"],
        })

    print(f"  Parsed {len(planes)} planes.")
    return planes


# ─────────────────────────────────────────────
# STEP 4: Parse Excel — Debriefs sheet
# ─────────────────────────────────────────────

def parse_debriefs(wb):
    """
    Reads the Debriefs sheet. Columns expected (0-indexed):
      0:  Tail Number
      1:  Plane Type
      2:  Service Location
      3:  Date
      4:  Technician
      5:  Interior Clean    (1 = done, 0 = not done)
      6:  Exterior Clean
      7:  Deep Seat Clean
      8:  Carpet Extraction
      9:  Biohazard
      10: Other
      11: Notes
      12: Sub ID
      13: Raw Link          (SharePoint PDF link)
    """
    ws = wb["Debriefs"]
    debriefs = []

    rows = list(ws.iter_rows(values_only=True))
    print(f"  Debriefs sheet: {len(rows)-1} data rows")

    for row in rows[1:]:
        tail = row[0]
        if not tail:
            continue

        # Parse date
        date_val = row[3]
        if isinstance(date_val, datetime):
            date_str = date_val.date().isoformat()
        elif isinstance(date_val, date):
            date_str = date_val.isoformat()
        else:
            date_str = None

        # Service flags — treat any truthy non-zero value as done
        def flag(v):
            try:
                return 1 if int(v) >= 1 else 0
            except (TypeError, ValueError):
                return 0

        debriefs.append({
            "tail":     str(tail),
            "type":     str(row[1] or ""),
            "location": str(row[2] or ""),
            "date":     date_str,
            "tech":     str(row[4] or ""),
            "IC":       flag(row[5]),
            "EC":       flag(row[6]),
            "DSC":      flag(row[7]),
            "CE":       flag(row[8]),
            "biohazard":flag(row[9]),
            "other":    flag(row[10]),
            "notes":    str(row[11] or ""),
            "subId":    str(row[12] or ""),
            "link":     str(row[13] or "") if row[13] else None,
        })

    print(f"  Parsed {len(debriefs)} debrief records.")
    return debriefs


# ─────────────────────────────────────────────
# STEP 5: Write data.json
# ─────────────────────────────────────────────

def write_json(planes, debriefs, requests=None):
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "planes":    planes,
        "debriefs":  debriefs,
        "requests":  requests or [],
    }
    with open("data.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"  Written: data.json  ({len(planes)} planes, {len(debriefs)} debriefs, {len(requests or [])} active requests)")




# ─────────────────────────────────────────────
# STEP 6: Process service requests
# ─────────────────────────────────────────────

def process_requests(debriefs):
    """
    Reads requests.json, checks each open request for fulfillment
    against debriefs since the request date, and returns active requests.

    Fulfillment: all requested services must appear (flag=1) across
    debriefs for that tail on or after the requestDate.
    """
    req_path = "requests.json"
    if not os.path.exists(req_path):
        print("  No requests.json found — skipping")
        return []

    with open(req_path) as f:
        data = json.load(f)

    all_reqs = data.get("requests", [])
    active = []

    for req in all_reqs:
        if req.get("status") != "open":
            continue

        tail     = req["tail"]
        req_date = req["requestDate"]
        services = req["services"]

        # Find debriefs for this tail on or after requestDate
        relevant = [d for d in debriefs if d["tail"] == tail and (d["date"] or "") >= req_date]

        # Union of services performed across relevant debriefs
        done = set()
        for d in relevant:
            for svc in services:
                if d.get(svc) == 1:
                    done.add(svc)

        if all(s in done for s in services):
            print(f"  Request {req['requestId']} FULFILLED — {tail} {services}")
            req["status"] = "fulfilled"
            # Write back fulfilled status
            data["requests"] = all_reqs
            with open(req_path, "w") as f:
                json.dump(data, f, indent=2)
        else:
            active.append(req)

    print(f"  Active requests: {len(active)} of {len(all_reqs)} total")
    return active


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        import openpyxl
    except ImportError:
        print("ERROR: openpyxl not installed. Run: pip install openpyxl requests")
        sys.exit(1)

    print("=== JSX Compliance Data Relay ===")
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    token     = get_token()
    xlsx_path = download_excel(token)

    print("\nParsing workbook...")
    wb       = openpyxl.load_workbook(xlsx_path, data_only=True)
    planes   = parse_planes(wb)
    debriefs = parse_debriefs(wb)

    print("\nWriting data.json...")
    print("\nProcessing service requests...")
    active_requests = process_requests(debriefs)
    write_json(planes, debriefs, active_requests)

    print("\n=== Done ===")
