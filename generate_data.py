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

def get_graph_token():
    """Get Microsoft Graph API token for sending emails."""
    import requests as req_lib
    r = req_lib.post(
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/v2.0/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "scope":         "https://graph.microsoft.com/.default",
        }
    )
    r.raise_for_status()
    return r.json()["access_token"]


def send_email(token, to_addresses, subject, body_html):
    """Send email via Graph API from foxtrot.automation."""
    import requests as req_lib
    recipients = [{"emailAddress": {"address": a}} for a in to_addresses]
    r = req_lib.post(
        "https://graph.microsoft.com/v1.0/users/foxtrot.automation@foxtrotaviation.com/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": recipients,
            }
        }
    )
    return r.status_code


SVC_NAMES = {
    "IC": "Interior Detail",
    "EC": "Exterior Detail",
    "DSC": "Deep Seat Clean",
    "CE": "Carpet Extraction",
}

DISTRO = "jsx.requests@foxtrotaviation.com"


def process_requests(debriefs):
    """
    Reads requests.json, checks each open request for fulfillment
    against debriefs since the request date, and returns active requests.

    Fulfillment: all requested services must appear (flag=1) across
    debriefs for that tail on or after the requestDate.

    Also sends:
      - Fulfillment email to the requestor when all services are complete
      - Warning email to the distro if any request is due in 1 day
    """
    req_path = "requests.json"
    if not os.path.exists(req_path):
        print("  No requests.json found — skipping")
        return []

    with open(req_path) as f:
        data = json.load(f)

    all_reqs = data.get("requests", [])
    active = []
    changed = False

    # Get Graph token once — only if we have credentials
    token = None
    has_creds = all(k in os.environ for k in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"))
    if has_creds:
        try:
            token = get_graph_token()
        except Exception as e:
            print(f"  Warning: Could not get Graph token for emails: {e}")

    today = date.today()

    for req in all_reqs:
        if req.get("status") != "open":
            continue

        tail      = req["tail"]
        req_date  = req["requestDate"]
        services  = req["services"]
        req_id    = req["requestId"]

        # Find debriefs for this tail on or after requestDate
        relevant = [d for d in debriefs if d["tail"] == tail and (d["date"] or "") >= req_date]

        # Union of services performed
        done = set()
        for d in relevant:
            for svc in services:
                if d.get(svc) == 1:
                    done.add(svc)

        if all(s in done for s in services):
            # ── FULFILLED ──────────────────────────────────
            print(f"  Request {req_id} FULFILLED — {tail} {services}")
            req["status"] = "fulfilled"
            changed = True

            if token:
                try:
                    svcs_str = ", ".join(SVC_NAMES.get(s, s) for s in services)
                    to = [req["requestorEmail"]]
                    if req.get("additionalEmail"):
                        to.append(req["additionalEmail"])

                    body = f"""
<p>Good news — your service request for <strong>{tail}</strong> has been fulfilled.</p>
<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;margin-top:12px">
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#f9f9f9">Tail Number</td><td style="padding:6px 14px">{tail}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555">Services Completed</td><td style="padding:6px 14px">{svcs_str}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#f9f9f9">Originally Requested By</td><td style="padding:6px 14px;background:#f9f9f9">{req_date}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555">Request ID</td><td style="padding:6px 14px;color:#888;font-size:12px">{req_id}</td></tr>
</table>
<p style="margin-top:16px">All requested services have been recorded in the Foxtrot JSX Compliance Tracker. No further action is needed.</p>
<p style="margin-top:16px;color:#888;font-size:12px">— Foxtrot Aviation Services JSX Compliance Tracker</p>
"""
                    code = send_email(token, to, f"[JSX Request Fulfilled] {tail} — {svcs_str}", body)
                    print(f"  Fulfillment email sent to {to} (status {code})")
                except Exception as e:
                    print(f"  Fulfillment email error: {e}")

        else:
            # ── STILL OPEN — check 1-day warning ───────────
            try:
                req_dt = datetime.strptime(req_date, "%Y-%m-%d").date()
                days_until = (req_dt - today).days
            except Exception:
                days_until = None

            if days_until is not None and days_until == 1 and not req.get('warned'):
                # Due tomorrow — send warning to distro
                remaining = [s for s in services if s not in done]
                completed = [s for s in services if s in done]
                svcs_remaining = ", ".join(SVC_NAMES.get(s, s) for s in remaining)
                svcs_done      = ", ".join(SVC_NAMES.get(s, s) for s in completed) if completed else "None"

                if token:
                    try:
                        body = f"""
<p><strong>⚠ Warning:</strong> The following service request is due <strong>tomorrow ({req_date})</strong> and has not yet been fully completed.</p>
<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;margin-top:12px">
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#fff8f0">Tail Number</td><td style="padding:6px 14px;background:#fff8f0">{tail}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555">Services Still Needed</td><td style="padding:6px 14px;color:#D97706;font-weight:bold">{svcs_remaining}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#fff8f0">Services Completed</td><td style="padding:6px 14px;background:#fff8f0">{svcs_done}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555">Due Date</td><td style="padding:6px 14px">{req_date}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#fff8f0">Requested By</td><td style="padding:6px 14px;background:#fff8f0">{req["requestorName"]} ({req["requestorEmail"]})</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555">Location</td><td style="padding:6px 14px">{req.get("location") or "Not specified"}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#555;background:#fff8f0">Notes</td><td style="padding:6px 14px;background:#fff8f0">{req.get("notes") or "—"}</td></tr>
</table>
<p style="margin-top:16px;color:#888;font-size:12px">— Foxtrot Aviation Services JSX Compliance Tracker</p>
"""
                        code = send_email(token, [DISTRO],
                            f"[JSX ⚠ Due Tomorrow] {tail} — {svcs_remaining}", body)
                        print(f"  1-day warning email sent for {req_id} (status {code})")
                        req['warned'] = True
                        changed = True
                    except Exception as e:
                        print(f"  Warning email error: {e}")

            active.append(req)

    # Write back if any requests were fulfilled
    if changed:
        data["requests"] = all_reqs
        with open(req_path, "w") as f:
            json.dump(data, f, indent=2)
        print("  requests.json updated with fulfilled statuses")

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
