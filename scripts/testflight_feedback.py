#!/usr/bin/env python3
"""Download TestFlight beta feedback (screenshot + crash submissions) from App Store Connect.

Config is read from ~/.appstoreconnect/config.json (or env vars of the same names):
    {"ASC_KEY_ID": "...", "ASC_ISSUER_ID": "...", "ASC_KEY_PATH": "~/.appstoreconnect/AuthKey.p8"}

Output goes to .local/testflight-feedback/ (gitignored):
    feedback.json, feedback.md, screenshots/, crashlogs/, handled.json

Usage:
    python3 scripts/testflight_feedback.py            # fetch; feedback.md lists unhandled items only
    python3 scripts/testflight_feedback.py --all      # include already-handled items in feedback.md
    python3 scripts/testflight_feedback.py --mark ID fixed "commit abc123"   # record an item as handled

Requires PyJWT with cryptography: pip install 'pyjwt[crypto]'
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jwt

API = "https://api.appstoreconnect.apple.com"
CONFIG_PATH = Path("~/.appstoreconnect/config.json").expanduser()
DEFAULT_BUNDLE_ID = "com.cali-inc.Cali"  # App Store Connect name: "Cali Social"
OUT_DIR = Path(__file__).resolve().parent.parent / ".local" / "testflight-feedback"


def load_config():
    config = {}
    if CONFIG_PATH.exists():
        config.update(json.loads(CONFIG_PATH.read_text()))
    for key in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    config.setdefault("ASC_KEY_PATH", "~/.appstoreconnect/AuthKey.p8")
    missing = [k for k in ("ASC_KEY_ID", "ASC_ISSUER_ID") if not config.get(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} in {CONFIG_PATH} or environment.")
    return config


def make_token(config):
    key = Path(config["ASC_KEY_PATH"]).expanduser().read_text()
    now = int(time.time())
    return jwt.encode(
        {"iss": config["ASC_ISSUER_ID"], "iat": now, "exp": now + 15 * 60, "aud": "appstoreconnect-v1"},
        key,
        algorithm="ES256",
        headers={"kid": config["ASC_KEY_ID"], "typ": "JWT"},
    )


class Client:
    def __init__(self, token):
        self.token = token

    def get(self, url, params=None):
        if not url.startswith("http"):
            url = API + url
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="replace")
            raise RuntimeError(f"GET {url} -> {err.code}: {body}") from None

    def get_all(self, url, params=None):
        """Follow pagination; returns (data, included)."""
        data, included = [], []
        page = self.get(url, params)
        while True:
            data.extend(page.get("data", []))
            included.extend(page.get("included", []))
            next_url = page.get("links", {}).get("next")
            if not next_url:
                return data, included
            page = self.get(next_url)


def find_app(client, bundle_id):
    apps = client.get("/v1/apps", {"filter[bundleId]": bundle_id})["data"]
    if not apps:
        sys.exit(f"No app with bundle ID {bundle_id} visible to this API key.")
    return apps[0]


def fetch_submissions(client, app_id, kind):
    url = f"/v1/apps/{app_id}/{kind}"
    params = {"limit": 200, "sort": "-createdDate", "include": "build,tester"}
    try:
        data, included = client.get_all(url, params)
    except RuntimeError:
        # Fall back if includes are rejected for this endpoint.
        params.pop("include")
        data, included = client.get_all(url, params)
    lookup = {(i["type"], i["id"]): i["attributes"] for i in included}
    for item in data:
        rels = item.get("relationships", {})
        build = (rels.get("build") or {}).get("data")
        tester = (rels.get("tester") or {}).get("data")
        if build:
            item["_build"] = lookup.get((build["type"], build["id"]), {})
        if tester:
            item["_tester"] = lookup.get((tester["type"], tester["id"]), {})
    return data


def download(url, dest):
    # curl rather than urllib: urllib stalls on Apple's tf-feedback image host.
    if dest.exists():
        return
    subprocess.run(["curl", "-sSf", "-m", "60", "-o", str(dest), url], check=True)


def load_handled():
    path = OUT_DIR / "handled.json"
    return json.loads(path.read_text()) if path.exists() else {}


def mark(item_id, status, note):
    handled = load_handled()
    handled[item_id] = {"status": status, "note": note, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "handled.json").write_text(json.dumps(handled, indent=2))
    print(f"{item_id}: {status}")


def describe(item):
    a = item["attributes"]
    tester = item.get("_tester", {})
    who = " ".join(filter(None, [tester.get("firstName"), tester.get("lastName")])) or a.get("email") or "unknown tester"
    build = item.get("_build", {}).get("version")
    device = ", ".join(filter(None, [a.get("deviceModel"), a.get("osVersion") and f"iOS {a['osVersion']}"]))
    lines = [
        f"- **When:** {a.get('createdDate')}",
        f"- **Tester:** {who}" + (f" ({a['email']})" if a.get("email") and a.get("email") not in who else ""),
        f"- **Build:** {build or 'unknown'}",
        f"- **Device:** {device or 'unknown'}",
    ]
    comment = (a.get("comment") or "").strip()
    lines.append(f"- **Comment:** {comment if comment else '_(none)_'}")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID)
    parser.add_argument("--no-attachments", action="store_true", help="skip downloading screenshots and crash logs")
    parser.add_argument("--all", action="store_true", help="include already-handled items in feedback.md")
    parser.add_argument("--mark", nargs="+", metavar=("ID", "STATUS"),
                        help="record ID as handled with STATUS (fixed/wontfix/duplicate/...) and optional note, then exit")
    args = parser.parse_args()

    if args.mark:
        if len(args.mark) < 2:
            parser.error("--mark needs ID and STATUS")
        mark(args.mark[0], args.mark[1], " ".join(args.mark[2:]))
        return

    client = Client(make_token(load_config()))
    app = find_app(client, args.bundle_id)
    screenshots = fetch_submissions(client, app["id"], "betaFeedbackScreenshotSubmissions")
    crashes = fetch_submissions(client, app["id"], "betaFeedbackCrashSubmissions")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot_dir = OUT_DIR / "screenshots"
    crash_dir = OUT_DIR / "crashlogs"
    shot_dir.mkdir(exist_ok=True)
    crash_dir.mkdir(exist_ok=True)

    handled = load_handled()
    total = (len(screenshots), len(crashes))
    if not args.all:
        screenshots = [i for i in screenshots if i["id"] not in handled]
        crashes = [i for i in crashes if i["id"] not in handled]

    md = [f"# TestFlight feedback — {app['attributes']['name']}", ""]
    md += [f"## Screenshot feedback ({len(screenshots)})", ""]
    for item in screenshots:
        md += [f"### {item['id']}"] + describe(item)
        if item["id"] in handled:
            md.append(f"- **Handled:** {handled[item['id']]['status']} {handled[item['id']].get('note', '')}")
        if not args.no_attachments:
            for n, shot in enumerate(item["attributes"].get("screenshots") or []):
                ext = Path(urllib.parse.urlparse(shot["url"]).path).suffix or ".jpg"
                dest = shot_dir / f"{item['id']}-{n}{ext}"
                try:
                    download(shot["url"], dest)
                    md.append(f"- **Screenshot:** [{dest.name}](screenshots/{dest.name})")
                except Exception as err:  # URLs expire; keep going
                    md.append(f"- **Screenshot:** download failed ({err})")
        md.append("")

    md += [f"## Crash feedback ({len(crashes)})", ""]
    for item in crashes:
        md += [f"### {item['id']}"] + describe(item)
        if item["id"] in handled:
            md.append(f"- **Handled:** {handled[item['id']]['status']} {handled[item['id']].get('note', '')}")
        if not args.no_attachments:
            dest = crash_dir / f"{item['id']}.crash"
            try:
                if not dest.exists():
                    log = client.get(f"/v1/betaFeedbackCrashSubmissions/{item['id']}/crashLog")
                    dest.write_text(log["data"]["attributes"].get("logText") or "")
                md.append(f"- **Crash log:** [{dest.name}](crashlogs/{dest.name})")
            except Exception as err:
                md.append(f"- **Crash log:** download failed ({err})")
        md.append("")

    (OUT_DIR / "feedback.json").write_text(
        json.dumps({"app": app, "screenshots": screenshots, "crashes": crashes}, indent=2)
    )
    (OUT_DIR / "feedback.md").write_text("\n".join(md))
    print(f"{total[0]} screenshot / {total[1]} crash submissions total; "
          f"{len(screenshots)} / {len(crashes)} written to {OUT_DIR / 'feedback.md'}"
          + ("" if args.all else " (unhandled only)"))


if __name__ == "__main__":
    main()
