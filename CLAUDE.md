# jsx-compliance-tracker

**Owner:** Samuel Kosco — Data Analyst, Foxtrot Aviation Services  
**Repo:** `sam-kosco/jsx-compliance-tracker`  
**Hosted at:** `sam-kosco.github.io/jsx-compliance-tracker/`  
**Access:** Password-protected — password is `JSX2026`

This repo hosts the JSX Air fleet detailing compliance dashboard. It is client-facing — JSX operations staff have access. It includes a Fleet Tracker, Data Extraction tab, and Service Requests tab.

---

## Repository Structure

```
jsx-compliance-tracker/
├── index.html              # Full dashboard (Fleet Tracker + Data Extraction + Service Requests)
├── data.json               # Compliance data (auto-generated hourly)
├── requests.json           # Open service requests (managed by manage_requests.yml)
├── generate_data.py        # Data relay + fulfillment check script
└── .github/
    └── workflows/
        ├── data_refresh.yml        # Hourly cron — downloads Excel, calculates compliance
        └── manage_requests.yml     # workflow_dispatch — create / update / delete service requests
```

---

## How It Works

### Compliance Data Pipeline
1. Field techs submit JotForm debriefs after each service on a JSX aircraft
2. Power Automate appends submissions to `Power Flows/JSX/JSX Master Sheet.xlsx` on SharePoint
3. `data_refresh.yml` runs hourly via cron
4. `generate_data.py` downloads the Excel via Microsoft Graph API, calculates compliance windows for all 11 tails, checks open service requests for fulfillment, and writes `data.json`
5. GitHub commits `data.json` and `requests.json`; GitHub Pages serves the updated dashboard

### Service Requests
Requests are created, edited, and deleted from the Service Requests tab in the dashboard. The dashboard calls the GitHub API to trigger `manage_requests.yml` via `workflow_dispatch`. The workflow:
1. Reads `requests.json`
2. Applies the action (`create`, `update`, or `delete`)
3. Sends email notifications via Microsoft Graph API
4. Commits updated `requests.json`

Fulfillment is checked on every hourly `data_refresh.yml` run — if all requested services appear in debriefs since the request date, the request is marked fulfilled and an email is sent to the requestor.

---

## Tracked Services

| Code | Display | Full Name | Cycle |
|------|---------|-----------|-------|
| IC | ID | Interior Detail | 30 days |
| EC | ED | Exterior Detail | 90 days |
| DSC | DSC | Deep Seat Clean | 30 days |
| CE | CE | Carpet Extraction | 90 days |

> **Note:** The data layer uses `IC` and `EC` as keys throughout the JSON, Python, and Excel. The display labels `ID` and `ED` are cosmetic only — applied in the HTML dashboard JavaScript.

---

## Compliance Logic

```
Window = Cycle Length - Days Since Last Service
```

| Status | Condition |
|--------|-----------|
| Noncompliant | Any tracked job window < 0 OR "No Service" |
| Due Soon | No jobs noncompliant AND at least one job ≤ 7 days |
| Compliant | All tracked job windows > 7 days |

---

## Fleet

11 tails, all EMB 145. Tail list is maintained in the `Tail List` sheet of the JSX Master Sheet Excel on SharePoint.

---

## Service Requests (`requests.json`)

```json
{
  "requests": [
    {
      "requestId": "req_1714500000000",
      "tail": "N241JX",
      "requestDate": "2026-05-20",
      "services": ["IC", "DSC"],
      "requestorName": "Ty Johnson",
      "requestorEmail": "ty@jsx.com",
      "additionalEmail": "",
      "location": "OAK",
      "notes": "Before fleet review",
      "submittedAt": "2026-05-15T14:00:00Z",
      "status": "open",
      "warned": false
    }
  ]
}
```

**Status values:** `open`, `fulfilled`

**`warned` flag:** Set to `true` by `generate_data.py` when a 1-day warning email has been sent. Prevents repeat hourly warnings for the same request.

**Request date rule:** Must always be a future date (minimum tomorrow). Enforced in the dashboard form — cannot be set to today or earlier.

---

## Emails

All emails are sent from `foxtrot.automation@foxtrotaviation.com` via Microsoft Graph API.

| Trigger | Recipients |
|---------|-----------|
| Request created (from `manage_requests.yml`) | Requestor + additional CC email (confirmation) AND `jsx.requests@foxtrotaviation.com` (distro notification) |
| Request updated (from `manage_requests.yml`) | `jsx.requests@foxtrotaviation.com` |
| Request deleted (from `manage_requests.yml`) | `jsx.requests@foxtrotaviation.com` |
| Request fulfilled (from `generate_data.py`) | Requestor + additional CC email |
| Request due in 1 day — unmet (from `generate_data.py`) | `jsx.requests@foxtrotaviation.com` (sent once only — `warned` flag prevents repeats) |

Distro membership for `jsx.requests@foxtrotaviation.com` is managed in Microsoft 365 Admin.

---

## GitHub Secrets

| Secret | Description |
|--------|-------------|
| `TENANT_ID` | `ede0c57f-549f-4a90-9f8c-7ea130346f95` — Microsoft Entra tenant |
| `CLIENT_ID` | `58191600-ab56-4141-bff6-806805fcbff4` — Foxtrot Report Automation app |
| `CLIENT_SECRET` | App secret — **expires every 24 months**, set a renewal reminder |

**SharePoint Drive ID:** `b!_bzXaIx86kOufgJN3ih-BaDIDthKYuxJkJtLi1Bm5irGjCEnK-VHSpBRRm3_SDKU`  
**SharePoint Excel path:** `Power Flows/JSX/JSX Master Sheet.xlsx`

---

## Dashboard PAT (GitHub Personal Access Token)

The dashboard calls GitHub's workflow dispatch API to trigger `manage_requests.yml` when a service request is submitted, edited, or deleted. The PAT is stored directly in `index.html` as:

```javascript
const GH_PAT = 'ghp_xxxxxxxxxxxxxxxxxxxx';
```

- **Scope:** `workflow` only
- **Expiry:** 1 year — update annually in `index.html` and re-upload
- This PAT is intentionally in the client-side HTML. It is scoped to workflow dispatch only and cannot read or write any data directly. The dashboard is password-protected.

---

## Workflows

### `data_refresh.yml`
- **Trigger:** Hourly cron + manual dispatch
- **Script:** `generate_data.py`
- **What it does:** Downloads JSX Master Sheet from SharePoint, calculates compliance for all 11 tails, checks service request fulfillment, sends fulfillment and 1-day warning emails as needed, writes `data.json` and `requests.json`, commits both

### `manage_requests.yml`
- **Trigger:** `workflow_dispatch` only — called by the dashboard via GitHub API
- **Inputs:** `action` (`create` / `update` / `delete`), `payload` (JSON string)
- **What it does:** Reads `requests.json`, applies the action, sends appropriate emails, commits updated `requests.json`

---

## Data Tabs

### Tab 1 — Fleet Tracker
- Stat strip (total, noncompliant, due soon, compliant)
- Per-service compliance breakdown cards (IC/EC/DSC/CE %)
- Tail number lookup with searchable dropdown and detail panel
- Cued Services widget (shows open service requests — hidden when empty)
- Noncompliant and Due Soon plane card lists
- Full fleet table

### Tab 2 — Data Extraction
- Date range filter (default: 2026-01-01 to today EST)
- Location, tail, and service filters
- Sortable results table
- Export to CSV button

### Tab 3 — Service Requests
- Form to submit new requests (mandatory: tail, date, services, name, email)
- Edit mode pre-populates the form for existing requests
- Cancel Edit button appears in edit mode
- Submission status feedback (loading / success / error)

---

## Locations

JSX operates at: `PBI`, `OAK`, `BUR`, `LAS`, `DAL`, `SMO`, `OPF`

---

## Troubleshooting

### Dashboard shows stale data
`data.json` is outdated or missing. Go to **Actions → JSX Compliance Data Refresh → Run workflow**.

### Service request submission returns 401
The GH_PAT in `index.html` has expired or is invalid. Generate a new PAT at github.com → Settings → Developer settings → Personal access tokens (classic) → `workflow` scope only → update in `index.html`.

### Service request submission returns 404
The workflow file `manage_requests.yml` doesn't exist in `.github/workflows/` or the `GH_REPO` constant in `index.html` points to the wrong repo.

### GitHub Actions fails with 401 Unauthorized (data_refresh)
`CLIENT_SECRET` has expired. Renew in Microsoft Entra → App registrations → Foxtrot Report Automation → Certificates & secrets. Update the `CLIENT_SECRET` GitHub Secret here and in `sam-kosco/envoy-compliance-tracker`.

### GitHub Actions fails with 404 (file download)
Excel moved on SharePoint. Update `FILE_PATH` in `generate_data.py` and commit.

### Fulfillment emails not sending
Check that `TENANT_ID`, `CLIENT_ID`, and `CLIENT_SECRET` secrets are all set correctly. The email logic is non-fatal — a Graph API failure logs a warning but does not fail the workflow run.

---

## Maintenance

| Task | When | Action |
|------|------|--------|
| Renew CLIENT_SECRET | Every 24 months | Entra → new secret → update GitHub Secret in this repo and envoy-compliance-tracker |
| Renew GH_PAT | Every 1 year | GitHub → new PAT (workflow scope) → update `const GH_PAT` in `index.html` |
| Add tail to fleet | As needed | Add to Tail List sheet in JSX Master Sheet on SharePoint |
| Change dashboard password | As needed | Update `const PASSWORD = 'JSX2026'` in `index.html` |
| Pause hourly refresh | As needed | Comment out `cron:` line in `data_refresh.yml` |
| Excel moved on SharePoint | If relocated | Update `FILE_PATH` in `generate_data.py` |

---

## Key Contacts

| Role | Name | Contact |
|------|------|---------|
| System owner / Data Analyst | Samuel Kosco | samuel.kosco@foxtrotaviation.com |
| JSX client contact | Ty (JSX operations) | jsx.requests@foxtrotaviation.com (distro) |
| Automation sender account | Foxtrot Automation | foxtrot.automation@foxtrotaviation.com |
