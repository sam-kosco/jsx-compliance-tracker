"""
Source freshness gate for the hourly refresh workflows.

Usage: python3 check_source_modified.py "<SharePoint path>" <state file>

Compares the SharePoint workbook's lastModifiedDateTime (one cheap Graph
metadata call, stdlib only — no pip install needed) against the watermark
committed at the last successful refresh. Writes changed=true/false and
mtime=<...> to GITHUB_OUTPUT; the workflow skips the download/parse/commit
steps when nothing changed, so a no-op run bills ~1 runner minute instead
of a full refresh.

Fails OPEN: any error (auth, network, missing metadata) reports
changed=true so a checker problem can never block a real refresh.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

DRIVE_ID = "b!_bzXaIx86kOufgJN3ih-BaDIDthKYuxJkJtLi1Bm5irGjCEnK-VHSpBRRm3_SDKU"


def get_mtime(sp_path):
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default",
    }).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}/oauth2/v2.0/token",
        data=body), timeout=30))["access_token"]
    encoded = "/".join(urllib.parse.quote(seg) for seg in sp_path.split("/"))
    meta = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://graph.microsoft.com/v1.0/drives/{DRIVE_ID}/root:/{encoded}",
        headers={"Authorization": f"Bearer {tok}"}), timeout=30))
    return meta["lastModifiedDateTime"]


def main():
    sp_path, state_file = sys.argv[1], sys.argv[2]
    try:
        mtime = get_mtime(sp_path)
        prev = ""
        if os.path.exists(state_file):
            prev = open(state_file).read().strip()
        changed = mtime != prev
        print(f"source modified: {mtime} | watermark: {prev or 'none'} | changed: {changed}")
    except Exception as e:  # fail open — never block a refresh
        print(f"freshness check failed ({e}) — treating as changed")
        mtime, changed = "", True
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"changed={'true' if changed else 'false'}\n")
        f.write(f"mtime={mtime}\n")


if __name__ == "__main__":
    main()
