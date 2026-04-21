"""
Discord webhook notifier — posts Go/No-Go results as rich embeds.
"""
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from go_no_go import Verdict

load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

COLOR_GO = 0x37B24D
COLOR_WATCH = 0xF59F00
COLOR_TEAMING = 0x7950F2
COLOR_NO_GO = 0xF03E3E
COLOR_DIGEST = 0x0E7AFE


def _fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return iso[:10]


def _days_left(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return None


def _verdict_emoji(verdict: str) -> str:
    return {"GO": "🟢", "WATCH": "🟡", "WATCH_TEAMING": "🤝", "NO-GO": "🔴"}.get(verdict, "⚪")


def build_target_embed(v: Verdict) -> dict:
    opp = v.raw
    due = opp.get("due_date")
    days = _days_left(due)
    agency = opp.get("federal_agency", {})
    agency_name = agency.get("name", "Federal Agency") if isinstance(agency, dict) else "Federal Agency"
    naics = opp.get("naics_category", {})
    naics_name = naics.get("name", "") if isinstance(naics, dict) else ""
    url = opp.get("govtribe_url", "")

    urgency = ""
    if days is not None:
        if days <= 3:
            urgency = f" 🚨 **{days}d LEFT**"
        elif days <= 7:
            urgency = f" ⚡ {days}d left"
        elif days <= 14:
            urgency = f" ⏳ {days}d left"

    color_map = {"GO": COLOR_GO, "WATCH": COLOR_WATCH, "WATCH_TEAMING": COLOR_TEAMING}
    color = color_map.get(v.verdict, COLOR_WATCH)

    fields = [
        {"name": "Agency", "value": agency_name, "inline": True},
        {"name": "Type", "value": opp.get("opportunity_type", "—"), "inline": True},
        {"name": "Set-Aside", "value": opp.get("set_aside_type", "—"), "inline": True},
        {"name": "Score", "value": f"{v.score}/100", "inline": True},
        {"name": "Due", "value": f"{_fmt_date(due)}{urgency}", "inline": True},
        {"name": "Posted", "value": _fmt_date(opp.get("posted_date")), "inline": True},
    ]

    if v.reason_summary:
        fields.append({"name": "Analysis", "value": v.reason_summary, "inline": False})

    if v.teaming_flag:
        fields.append({
            "name": "🤝 Teaming Required",
            "value": "Bonding capacity needed. Identify SB partner with Z1AA/Z2AA history before responding.",
            "inline": False,
        })

    if v.recommended_action:
        fields.append({"name": "Next Step", "value": v.recommended_action, "inline": False})

    if naics_name:
        fields.append({"name": "NAICS", "value": naics_name, "inline": False})

    return {
        "title": f"{_verdict_emoji(v.verdict)} [{v.verdict}] {v.name}",
        "url": url or None,
        "color": color,
        "fields": fields,
        "footer": {"text": f"TBG Pipeline • {opp.get('govtribe_id', '')} • {datetime.now().strftime('%d %b %Y %H:%M')} UTC"},
    }


def build_digest_embed(go_watch: list[Verdict], no_go: list[Verdict], dashboard_url: str) -> dict:
    go_count = len([v for v in go_watch if v.verdict == "GO"])
    watch_count = len([v for v in go_watch if v.verdict == "WATCH"])
    team_count = len([v for v in go_watch if v.verdict == "WATCH_TEAMING"])
    total = len(go_watch) + len(no_go)

    lines = [f"**{total}** scanned · **{len(go_watch)}** targets identified\n"]

    if go_watch:
        lines.append("**Top Targets:**")
        for v in go_watch[:6]:
            opp = v.raw
            days = _days_left(opp.get("due_date"))
            tag = f" ⚡ {days}d" if days is not None and days <= 7 else ""
            team_tag = " 🤝" if v.teaming_flag else ""
            url = opp.get("govtribe_url", "")
            name = v.name[:50]
            lines.append(f"• [{name}]({url}) — {v.verdict} · {v.score}/100{tag}{team_tag}")

    lines.append(f"\n📊 [View Full Dashboard]({dashboard_url})")

    return {
        "title": "📡 TBG Pipeline Scan — " + datetime.now().strftime("%d %b %Y"),
        "description": "\n".join(lines),
        "color": COLOR_DIGEST,
        "fields": [
            {"name": "🟢 GO", "value": str(go_count), "inline": True},
            {"name": "🟡 WATCH", "value": str(watch_count), "inline": True},
            {"name": "🤝 TEAMING", "value": str(team_count), "inline": True},
            {"name": "🔴 NO-GO", "value": str(len(no_go)), "inline": True},
        ],
        "footer": {"text": f"TBG Pipeline Scanner • {datetime.now().strftime('%d %b %Y %H:%M')} UTC"},
    }


async def _post_embed(embed: dict) -> bool:
    if not WEBHOOK_URL:
        print("  ⚠ DISCORD_WEBHOOK_URL not set")
        return False
    payload = {"embeds": [embed]}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(WEBHOOK_URL, json=payload)
        return resp.status_code in (200, 204)


async def notify_pipeline(go_watch: list[Verdict], no_go: list[Verdict], dashboard_url: str) -> None:
    import asyncio
    if not WEBHOOK_URL:
        print("  ⚠ DISCORD_WEBHOOK_URL not configured")
        return

    await _post_embed(build_digest_embed(go_watch, no_go, dashboard_url))

    for v in go_watch[:5]:
        await _post_embed(build_target_embed(v))
        await asyncio.sleep(0.5)

    print(f"  ✓ Discord: digest + {min(len(go_watch), 5)} target cards sent")
