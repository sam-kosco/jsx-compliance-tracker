"""
JSX Daily Compliance Report
===========================
Builds "JSX-FoxTrot Compliance MM-DD-YYYY.xlsx" — a per-tail snapshot of the
fleet tracker's compliance state — and emails it to EMAIL_LIST from
foxtrot.automation@foxtrotaviation.com via Microsoft Graph. Mirrors the PSA
daily report (envoy-compliance-tracker/psa_daily_report.py); built
2026-10-05 once the 90-day EC/CE windows had fully elapsed against the
March 2026 go-live, per the deferral note in CLAUDE.md.

Reads the COMMITTED data.json — deliberately does NOT re-run
generate_data.py: that script also checks service-request fulfillment and
sends requestor emails, and running it here without committing the updated
requests.json would make the hourly refresh re-send them. data.json is at
most an hour old (hourly data_refresh.yml), which is immaterial against
30/90-day cycles; the email notes the data timestamp.

Sheet layout (simplified per Dan DiGiambattista / Chris Stump, thread
"JSX-FoxTrot Compliance 10/05/2026", applied 2026-10-12): one row per
tail — Tail | Type | Station | Last Service | Non Compliant Services |
Compliant Services. The Tail cell is colored by overall status
(red/yellow/green); Non Compliant Services spells out the WHY
("ED (12d overdue), CE (never serviced)") on red fill; Compliant
Services lists the rest ("All" when everything is current) on green,
with due-soon services annotated "(due in Nd)". Rows sort noncompliant
first (most overdue on top), then due soon, then compliant. IC/EC are
shown under their dashboard display names ID/ED.

Fired by the platform dispatcher (Monitoring/schedules.json) daily at
9:09 AM Eastern via workflow_dispatch — no native cron, no 9-AM guard.

Env:
  TENANT_ID / CLIENT_ID / CLIENT_SECRET — Graph credentials (GitHub Secrets)
  RECIPIENTS_OVERRIDE — comma-separated list replacing EMAIL_LIST (testing)
  DRY_RUN=1           — build the workbook but do not email it
"""

import os
import sys
import json
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
# RECIPIENTS — edit this list to change who gets the daily report
# (internal only by design — Sam, 2026-10-05; JSX client staff use the
# password-gated dashboard / platform proxy instead)
# ─────────────────────────────────────────────

EMAIL_LIST = [
    "samuel.kosco@foxtrotaviation.com",
    "daniel.digiambattista@foxtrotaviation.com",
    "chris.stump@foxtrotaviation.com",
    "anthony.pentz@foxtrotaviation.com",
]

SENDER = "foxtrot.automation@foxtrotaviation.com"

# Data keys are IC/EC/CE; the dashboard displays IC as ID and EC as ED.
SERVICES  = ["IC", "EC", "CE"]
DISPLAY   = {"IC": "ID", "EC": "ED", "CE": "CE"}
SOON_DAYS = 7

# Classic Excel conditional-format palettes (fill, font)
GREEN  = ("C6EFCE", "006100")
YELLOW = ("FFEB9C", "9C6500")
RED    = ("FFC7CE", "9C0006")


def eastern_today():
    return datetime.now(ZoneInfo("America/New_York")).date()


def classify(window):
    """Map a compliance window to a status color pair."""
    if isinstance(window, str) or window < 0:   # "No Service" or overdue
        return RED
    if window <= SOON_DAYS:
        return YELLOW
    return GREEN


def overall(plane):
    """Tail-level status across all tracked services (tracker rules)."""
    windows = [plane[s] for s in SERVICES]
    if any(isinstance(w, str) or w < 0 for w in windows):
        return RED
    if any(w <= SOON_DAYS for w in windows):
        return YELLOW
    return GREEN


def split_services(plane):
    """(noncompliant_notes, compliant_notes) for one tail — the WHY columns.
    Noncompliant: "ED (12d overdue)" / "ID (never serviced)". Compliant:
    service name, annotated "(due in Nd)" when inside the due-soon window."""
    bad, good = [], []
    for s in SERVICES:
        w, name = plane[s], DISPLAY[s]
        if isinstance(w, str):
            bad.append(f"{name} (never serviced)")
        elif w < 0:
            bad.append(f"{name} ({-w}d overdue)")
        elif w <= SOON_DAYS:
            good.append(f"{name} (due in {w}d)")
        else:
            good.append(name)
    return bad, good


def severity(plane):
    """Sort key: noncompliant first (never-serviced outranks numeric overdue,
    then total days overdue — work-order convention), then due soon by the
    soonest window, then compliant."""
    windows = [plane[s] for s in SERVICES]
    never = sum(1 for w in windows if isinstance(w, str))
    overdue = sum(-w for w in windows if not isinstance(w, str) and w < 0)
    if never or overdue:
        return (0, -never, -overdue)
    soonest = min(w for w in windows)
    if soonest <= SOON_DAYS:
        return (1, soonest, 0)
    return (2, soonest, 0)


# ─────────────────────────────────────────────
# Build the workbook
# ─────────────────────────────────────────────

def build_workbook(planes, out_path):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fleet"

    headers = ["Tail", "Type", "Station", "Last Service",
               "Non Compliant Services", "Compliant Services"]
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin   = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center")

    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill, c.font, c.alignment, c.border = header_fill, header_font, center, border

    def put(cell, value, color=None, bold=False):
        cell.value = value
        if color:
            fill, font = color
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.font = Font(color=font, bold=bold)
        else:
            cell.font = Font(bold=bold)
        cell.alignment = center
        cell.border = border

    for r, p in enumerate(sorted(planes, key=severity), start=2):
        bad, good = split_services(p)
        put(ws.cell(row=r, column=1), p["tail"], overall(p), bold=True)
        put(ws.cell(row=r, column=2), p.get("type") or "—")
        put(ws.cell(row=r, column=3), p.get("station") or "—")
        ls = p.get("lastService")
        c = ws.cell(row=r, column=4)
        if ls:
            put(c, datetime.strptime(ls, "%Y-%m-%d").date())
            c.number_format = "mm/dd/yyyy"
        else:
            put(c, "No Service")
        put(ws.cell(row=r, column=5), ", ".join(bad) if bad else "None",
            RED if bad else None)
        put(ws.cell(row=r, column=6),
            "All" if len(good) == len(SERVICES) else (", ".join(good) or "None"),
            GREEN if good else None)

    ws.freeze_panes = "A2"
    widths = {"A": 12, "B": 12, "C": 12, "D": 14, "E": 42, "F": 34}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.auto_filter.ref = f"A1:F{len(planes) + 1}"

    wb.save(out_path)
    print(f"  Workbook written: {out_path} ({len(planes)} tails)")


# ─────────────────────────────────────────────
# Email via Microsoft Graph
# ─────────────────────────────────────────────

def get_token():
    import requests
    resp = requests.post(
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials",
              "client_id": os.environ["CLIENT_ID"],
              "client_secret": os.environ["CLIENT_SECRET"],
              "scope": "https://graph.microsoft.com/.default"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def service_breakdown(planes):
    """Per-service counts: {code: (compliant, due_soon, overdue_or_never)}."""
    out = {}
    for s in SERVICES:
        green = yellow = red = 0
        for p in planes:
            w = p[s]
            if isinstance(w, str) or w < 0:
                red += 1
            elif w <= SOON_DAYS:
                yellow += 1
            else:
                green += 1
        out[s] = (green, yellow, red)
    return out


def send_report(token, recipients, xlsx_path, filename, report_date, counts, planes, generated):
    import requests
    with open(xlsx_path, "rb") as f:
        payload = base64.b64encode(f.read()).decode()

    nc, soon, ok = counts
    total = len(planes)
    cycles = {"IC": "30-day", "EC": "90-day", "CE": "90-day"}
    svc_rows = ""
    for s in SERVICES:
        g, y, r = service_breakdown(planes)[s]
        pct = round(100 * (g + y) / total) if total else 0
        svc_rows += (
            f"<tr><td style=\"padding:6px 14px;font-weight:bold;background:#1F4E78;color:#fff\">{DISPLAY[s]} <span style=\"font-weight:normal\">({cycles[s]})</span></td>"
            f"<td style=\"padding:6px 14px;background:#C6EFCE;color:#006100;text-align:center\">{g}</td>"
            f"<td style=\"padding:6px 14px;background:#FFEB9C;color:#9C6500;text-align:center\">{y}</td>"
            f"<td style=\"padding:6px 14px;background:#FFC7CE;color:#9C0006;text-align:center\">{r}</td>"
            f"<td style=\"padding:6px 14px;text-align:center;font-weight:bold\">{pct}%</td></tr>"
        )
    stamp = f"<p style=\"color:#888;font-size:12px\">Data as of {generated} (refreshed hourly).</p>" if generated else ""
    body = f"""
<p>Attached is the JSX-FoxTrot compliance report for <strong>{report_date.strftime('%B %d, %Y')}</strong>.</p>
<p style="margin-bottom:4px;font-family:Arial,sans-serif"><strong>Fleet overall</strong> ({total} tails):</p>
<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px">
  <tr><td style="padding:6px 14px;font-weight:bold;color:#9C0006;background:#FFC7CE">Noncompliant</td><td style="padding:6px 14px">{nc}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#9C6500;background:#FFEB9C">Due Soon</td><td style="padding:6px 14px">{soon}</td></tr>
  <tr><td style="padding:6px 14px;font-weight:bold;color:#006100;background:#C6EFCE">Compliant</td><td style="padding:6px 14px">{ok}</td></tr>
</table>
<p style="margin:16px 0 4px;font-family:Arial,sans-serif"><strong>By tracked service</strong>:</p>
<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px">
  <tr>
    <td style="padding:6px 14px;background:#1F4E78;color:#fff;font-weight:bold">Service</td>
    <td style="padding:6px 14px;background:#1F4E78;color:#fff;font-weight:bold">Compliant</td>
    <td style="padding:6px 14px;background:#1F4E78;color:#fff;font-weight:bold">Due Soon</td>
    <td style="padding:6px 14px;background:#1F4E78;color:#fff;font-weight:bold">Overdue / Never</td>
    <td style="padding:6px 14px;background:#1F4E78;color:#fff;font-weight:bold">% In Compliance</td>
  </tr>
  {svc_rows}
</table>
<p style="margin-top:16px">The attached sheet lists one row per tail: the Tail cell is colored by overall status, <strong>Non Compliant Services</strong> says why a tail is out (service + how far overdue), and <strong>Compliant Services</strong> shows what's current (due-soon services annotated). "% In Compliance" counts compliant + due-soon (due soon is still within cycle).</p>
{stamp}
<p style="margin-top:16px;color:#888;font-size:12px">— Foxtrot Aviation Services JSX Compliance Tracker</p>
"""
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": {
            "subject": f"JSX-FoxTrot Compliance {report_date.strftime('%m/%d/%Y')}",
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": filename,
                "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "contentBytes": payload,
            }],
        }},
    )
    resp.raise_for_status()
    print(f"  Email sent to: {', '.join(recipients)}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=== JSX Daily Compliance Report ===")

    report_date = eastern_today()
    # Slashes are invalid in filenames, so the attachment uses dashes
    filename = f"JSX-FoxTrot Compliance {report_date.strftime('%m-%d-%Y')}.xlsx"
    out_path = f"/tmp/{filename}" if os.name != "nt" else filename

    with open("data.json") as f:
        data = json.load(f)
    planes = data["planes"]
    generated = data.get("generated", "")
    print(f"  Loaded data.json: {len(planes)} tails (generated {generated})")

    counts = (
        sum(1 for p in planes if overall(p) == RED),
        sum(1 for p in planes if overall(p) == YELLOW),
        sum(1 for p in planes if overall(p) == GREEN),
    )
    print(f"  Noncompliant: {counts[0]}  |  Due soon: {counts[1]}  |  OK: {counts[2]}")

    build_workbook(planes, out_path)

    if os.environ.get("DRY_RUN") == "1":
        print("  DRY_RUN=1 — skipping email.")
        sys.exit(0)

    override = os.environ.get("RECIPIENTS_OVERRIDE", "").strip()
    recipients = [a.strip() for a in override.split(",") if a.strip()] or EMAIL_LIST

    token = get_token()
    send_report(token, recipients, out_path, filename, report_date, counts, planes, generated)
    print("=== Done ===")
