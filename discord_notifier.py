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

# Embed colors (Discord uses decimal)
COLOR_GO = 0x37B24D           # green
COLOR_CONDITIONAL = 0xF59F00  # amber
COLOR_NO_GO = 0xF03E3E        # red
COLOR_DIGEST = 0x0E7AFE       # blue


def _format_date(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return iso[:10]


def _days_until(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = dt - datetime.now(timezone.utc)
        return delta.days
    except Exception:
        return None


def _verdict_emoji(verdict: str) -> str:
    return {"GO": "🟢", "CONDITIONAL GO": "🟡", "NO-GO": "🔴"}.get(verdict, "⚪")


def build_go_embed(v: Verdict) -> dict:
    """Build a rich Discord embed for a single GO opportunity."""
    opp = v.raw
    due = opp.get("due_date")
    posted = opp.get("posted_date")
    days_left = _days_until(due)
    agency = opp.get("federal_agency", {})
    agency_name = agency.get("name", "Federal Agency") if isinstance(agency, dict) else "Federal Agency"
    naics = opp.get("naics_category", {})
    naics_name = naics.get("name", "") if isinstance(naics, dict) else ""
    url = opp.get("govtribe_url", "")

    urgency = ""
    if days_left is not None:
        if days_left <= 3:
            urgency = f" 🚨 **{days_left}d REMAINING**"
        elif days_left <= 7:
            urgency = f" ⚡ {days_left}d left"
        elif days_left <= 14:
            urgency = f" ⏳ {days_left}d left"

    color = COLOR_GO if v.verdict == "GO" else COLOR_CONDITIONAL

    fields = [
        {"name": "Agency", "value": agency_name, "inline": True},
        {"name": "Type", "value": opp.get("opportunity_type", "—"), "inline": True},
        {"name": "Set-Aside", "value": opp.get("set_aside_type", "—"), "inline": True},
        {"name": "Posted", "value": _format_date(posted), "inline": True},
        {"name": "Due", "value": f"{_format_date(due)}{urgency}", "inline": True},
        {"name": "Signal Score", "value": f"{v.score}/100", "inline": True},
    ]

    if v.wedge_signals:
        fields.append({
            "name": "TBG Wedge Signals",
            "value": "• " + "\n• ".join(v.wedge_signals),
            "inline": False,
        })

    if v.warnings:
        fields.append({
            "name": "⚠ Warnings — Verify Before Bidding",
            "value": "\n".join(v.warnings),
            "inline": False,
        })

    if naics_name:
        fields.append({"name": "NAICS", "value": naics_name, "inline": False})

    return {
        "title": f"{_verdict_emoji(v.verdict)} [{v.verdict}] {v.name}",
        "url": url or None,
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"TBG Go/No-Go Engine • {opp.get('govtribe_id', '')} • {datetime.now().strftime('%d %b %Y %H:%M')} UTC"
        },
    }


def build_digest_embed(go_list: list[Verdict], no_go_list: list[Verdict]) -> dict:
    """Build a summary digest embed for the scan run."""
    total = len(go_list) + len(no_go_list)
    go_count = len([v for v in go_list if v.verdict == "GO"])
    cond_count = len([v for v in go_list if v.verdict == "CONDITIONAL GO"])

    lines = [f"**{total}** opportunities scanned · **{len(go_list)}** targets identified\n"]

    if go_list:
        lines.append("**🟢 GO Targets:**")
        for v in go_list[:8]:
            opp = v.raw
            due = opp.get("due_date")
            days = _days_until(due)
            tag = f" ⚡ {days}d" if days is not None and days <= 7 else ""
            lines.append(f"• [{v.name[:55]}]({opp.get('govtribe_url', '')}) — score {v.score}{tag}")

    return {
        "title": "📡 TBG Pipeline Scan Complete",
        "description": "\n".join(lines),
        "color": COLOR_DIGEST,
        "fields": [
            {"name": "🟢 GO", "value": str(go_count), "inline": True},
            {"name": "🟡 Conditional", "value": str(cond_count), "inline": True},
            {"name": "🔴 NO-GO", "value": str(len(no_go_list)), "inline": True},
        ],
        "footer": {"text": f"TBG Go/No-Go Engine • {datetime.now().strftime('%d %b %Y %H:%M')} UTC"},
    }


async def post_embed(embed: dict) -> bool:
    """POST a single embed to Discord."""
    if not WEBHOOK_URL:
        print("⚠ DISCORD_WEBHOOK_URL not set — skipping Discord notification.")
        return False
    payload = {"embeds": [embed]}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(WEBHOOK_URL, json=payload)
        if resp.status_code in (200, 204):
            return True
        print(f"Discord error {resp.status_code}: {resp.text}")
        return False


async def notify_pipeline(go_list: list[Verdict], no_go_list: list[Verdict]) -> None:
    """Post digest + individual GO embeds to Discord."""
    if not WEBHOOK_URL:
        print("⚠ DISCORD_WEBHOOK_URL not configured — set it in .env")
        return

    # 1. Digest summary
    digest = build_digest_embed(go_list, no_go_list)
    await post_embed(digest)

    # 2. Individual GO cards (limit to top 5 to avoid rate limiting)
    for v in go_list[:5]:
        embed = build_go_embed(v)
        await post_embed(embed)
        # Small delay to avoid Discord rate limiting
        import asyncio
        await asyncio.sleep(0.5)

    print(f"✓ Discord: posted digest + {min(len(go_list), 5)} GO cards")
