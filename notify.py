"""
TBG Notifier — reads pipeline.json and posts to Discord + pushes to GitHub.
Called by the Claude cron session after it writes pipeline.json.

Usage:
    python notify.py              # post to Discord + git push
    python notify.py --dry-run    # print digest only, no post or push
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
PIPELINE_JSON = Path(__file__).parent / "pipeline.json"
DASHBOARD_URL = "https://jrmann22.github.io/tbg-pipeline/"
DRY_RUN = "--dry-run" in sys.argv

COLOR_GO       = 0x3FB950
COLOR_WATCH    = 0xD29922
COLOR_TEAMING  = 0xA371F7
COLOR_DIGEST   = 0x0E7AFE


def fmt_date(s: Optional[str]) -> str:
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d %b %Y")
    except Exception:
        return s[:10]


def days_left(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return None


def urgency(due: Optional[str]) -> str:
    d = days_left(due)
    if d is None:
        return ""
    if d <= 3:
        return f" | URGENT {d}d left"
    if d <= 7:
        return f" | {d}d left"
    return ""


def build_digest(data: dict) -> dict:
    s = data.get("scan_summary", {})
    targets = data.get("targets", [])

    lines = [
        f"**{s.get('total_scanned', 0)}** opportunities scanned  |  "
        f"**{len(targets)}** targets identified\n"
    ]

    if targets:
        lines.append("**Active Targets:**")
        for t in targets[:8]:
            verdict = t.get("verdict", "")
            score = t.get("score", 0)
            name = t.get("name", "")[:52]
            url = t.get("govtribe_url", "")
            due = t.get("due_date", "")
            team = " [TEAMING]" if t.get("teaming_flag") else ""
            urg = urgency(due)
            link = f"[{name}]({url})" if url else name
            lines.append(f"• {link} — {verdict} {score}/100{team}{urg}")

    lines.append(f"\nDashboard: {DASHBOARD_URL}")

    return {
        "title": f"TBG Pipeline Scan — {datetime.now().strftime('%d %b %Y')}",
        "description": "\n".join(lines),
        "color": COLOR_DIGEST,
        "fields": [
            {"name": "GO",      "value": str(s.get("go", 0)),           "inline": True},
            {"name": "WATCH",   "value": str(s.get("watch", 0)),         "inline": True},
            {"name": "TEAMING", "value": str(s.get("watch_teaming", 0)), "inline": True},
            {"name": "NO-GO",   "value": str(s.get("no_go", 0)),         "inline": True},
            {"name": "FORECAST","value": str(s.get("forecast", 0)),      "inline": True},
        ],
        "footer": {"text": f"TBG Pipeline • {datetime.now().strftime('%d %b %Y %H:%M')} UTC"},
    }


def build_target_embed(t: dict) -> dict:
    verdict = t.get("verdict", "WATCH")
    color = {"GO": COLOR_GO, "WATCH": COLOR_WATCH, "WATCH_TEAMING": COLOR_TEAMING}.get(verdict, COLOR_WATCH)
    label = "TEAMING" if verdict == "WATCH_TEAMING" else verdict
    fields = [
        {"name": "Agency",    "value": t.get("agency", "—"),        "inline": True},
        {"name": "Set-Aside", "value": t.get("set_aside_type", "—"), "inline": True},
        {"name": "Score",     "value": f"{t.get('score', 0)}/100",  "inline": True},
        {"name": "Due",       "value": fmt_date(t.get("due_date")),  "inline": True},
        {"name": "NAICS",     "value": t.get("naics", "—"),         "inline": True},
    ]
    if t.get("reason_summary"):
        fields.append({"name": "Analysis", "value": t["reason_summary"], "inline": False})
    if t.get("teaming_flag"):
        fields.append({"name": "Teaming Required", "value": "Identify SB partner with bonding capacity before responding.", "inline": False})
    if t.get("recommended_action"):
        fields.append({"name": "Next Step", "value": t["recommended_action"], "inline": False})
    return {
        "title": f"[{label}] {t.get('name', '')}",
        "url": t.get("govtribe_url") or None,
        "color": color,
        "fields": fields,
        "footer": {"text": f"TBG Pipeline • score {t.get('score', 0)}/100"},
    }


async def post_embed(embed: dict) -> bool:
    if not WEBHOOK_URL:
        print("  DISCORD_WEBHOOK_URL not set")
        return False
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(WEBHOOK_URL, json={"embeds": [embed]})
        return r.status_code in (200, 204)


def git_push() -> bool:
    repo = Path(__file__).parent
    try:
        subprocess.run(["git", "add", "pipeline.json"], cwd=repo, check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo, capture_output=True)
        if diff.returncode == 0:
            print("  No changes — skipping git push")
            return True
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "commit", "-m", f"chore: pipeline scan {ts}"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=repo, check=True, capture_output=True)
        print("  Git push OK")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Git push failed: {e}")
        return False


async def main():
    if not PIPELINE_JSON.exists():
        print("pipeline.json not found — run the Claude cron scan first")
        sys.exit(1)

    data = json.loads(PIPELINE_JSON.read_text())
    targets = data.get("targets", [])
    s = data.get("scan_summary", {})

    print(f"Pipeline: GO={s.get('go',0)} WATCH={s.get('watch',0)} TEAMING={s.get('watch_teaming',0)} NO-GO={s.get('no_go',0)} FORECAST={s.get('forecast',0)}")

    if DRY_RUN:
        print("DRY RUN — skipping Discord and git push")
        return

    # Discord: digest only
    ok = await post_embed(build_digest(data))
    print(f"  Discord digest: {'OK' if ok else 'FAILED'}")

    # GitHub Pages
    git_push()


if __name__ == "__main__":
    asyncio.run(main())
