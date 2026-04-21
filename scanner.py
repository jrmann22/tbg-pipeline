"""
TBG Pipeline Scanner — main orchestration script.

Usage:
    python scanner.py              # run once immediately
    python scanner.py --dry-run    # run without posting to Discord or pushing to GitHub

Scheduled via Claude Code cron at 07:00 EDT daily.
Dashboard published to: https://jrmann22.github.io/tbg-pipeline/
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from discord_notifier import notify_pipeline
from go_no_go import evaluate_batch
from govtribe_client import fetch_all_opportunities

load_dotenv()

PIPELINE_JSON = Path(__file__).parent / "pipeline.json"
DRY_RUN = "--dry-run" in sys.argv
DASHBOARD_URL = "https://jrmann22.github.io/tbg-pipeline/"


def _to_record(v) -> dict:
    """Convert a Verdict into a JSON-serializable pipeline record."""
    opp = v.raw
    agency = opp.get("federal_agency", {})
    naics = opp.get("naics_category", {})
    psc = opp.get("psc_category", {})
    return {
        "id": v.opportunity_id,
        "name": v.name,
        "verdict": v.verdict,
        "score": v.score,
        "kill_reason": v.kill_reason,
        "reason_summary": v.reason_summary,
        "recommended_action": v.recommended_action,
        "bonding_required": v.bonding_required,
        "teaming_flag": v.teaming_flag,
        "priority_agency": v.priority_agency,
        "score_breakdown": v.score_breakdown,
        "agency": agency.get("name", "") if isinstance(agency, dict) else "",
        "agency_url": agency.get("govtribe_url", "") if isinstance(agency, dict) else "",
        "opportunity_type": opp.get("opportunity_type", ""),
        "set_aside_type": opp.get("set_aside_type", ""),
        "posted_date": (opp.get("posted_date", "") or "")[:10],
        "due_date": (opp.get("due_date", "") or "")[:10],
        "govtribe_url": opp.get("govtribe_url", ""),
        "naics": naics.get("name", "") if isinstance(naics, dict) else "",
        "psc": psc.get("govtribe_id", "") if isinstance(psc, dict) else "",
    }


def _to_forecast_record(award: dict) -> dict:
    """Convert an expiring award into a forecast pipeline record."""
    agency = award.get("funding_federal_agency", {})
    naics = award.get("naics_category", {})
    awardee = award.get("awardee", {})
    end_date = (award.get("end_date", "") or "")[:10]

    # Recommend action date: 9 months before end date
    action_date = ""
    if end_date:
        try:
            from datetime import timedelta
            end_dt = datetime.fromisoformat(end_date)
            action_dt = end_dt - timedelta(days=270)
            action_date = action_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return {
        "id": award.get("govtribe_id", ""),
        "name": award.get("name", ""),
        "agency": agency.get("name", "") if isinstance(agency, dict) else "",
        "incumbent": awardee.get("name", "") if isinstance(awardee, dict) else "",
        "current_value": award.get("ceiling_value") or award.get("dollars_obligated") or 0,
        "end_date": end_date,
        "action_date": action_date,
        "naics": naics.get("name", "") if isinstance(naics, dict) else "",
        "set_aside": award.get("set_aside_type", ""),
        "govtribe_url": award.get("govtribe_url", ""),
    }


def _git_push() -> bool:
    """Commit updated pipeline.json and push to GitHub Pages."""
    repo = Path(__file__).parent
    try:
        subprocess.run(["git", "add", "pipeline.json"], cwd=repo, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo, capture_output=True
        )
        if result.returncode == 0:
            print("  No changes to pipeline.json — skipping git push")
            return True
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"chore: daily pipeline scan {ts}"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=repo, check=True, capture_output=True)
        print(f"  ✓ Pushed pipeline.json → GitHub Pages")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Git push failed: {e.stderr.decode()}")
        return False


async def run_scan() -> dict:
    print(f"\n{'='*60}")
    print(f"TBG PIPELINE SCAN — {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(f"{'='*60}")

    # 1. Fetch opportunities + forecast
    print("\n[1/4] Fetching opportunities across all 4 saved searches...")
    live_opps, forecast_awards = await fetch_all_opportunities()
    print(f"      {len(live_opps)} unique live opportunities")
    print(f"      {len(forecast_awards)} expiring contracts (forecast layer)")

    # 2. Go/No-Go filter
    print("\n[2/4] Running Go/No-Go analysis...")
    go_watch, no_go = evaluate_batch(live_opps)
    go_count = len([v for v in go_watch if v.verdict == "GO"])
    watch_count = len([v for v in go_watch if v.verdict == "WATCH"])
    team_count = len([v for v in go_watch if v.verdict == "WATCH_TEAMING"])
    print(f"      🟢 GO: {go_count}  🟡 WATCH: {watch_count}  🤝 TEAMING: {team_count}  🔴 NO-GO: {len(no_go)}")

    if go_watch:
        print("\n── TARGETS ──")
        for v in go_watch[:8]:
            due = v.raw.get("due_date", "")[:10]
            flag = " 🤝" if v.teaming_flag else ""
            print(f"  [{v.verdict:14s}] score={v.score:3d}  due={due or 'open':10s}  {v.name[:60]}{flag}")

    # 3. Build pipeline.json
    print("\n[3/4] Writing pipeline.json...")
    now_iso = datetime.now(timezone.utc).isoformat()
    pipeline = {
        "generated_at": now_iso,
        "dashboard_url": DASHBOARD_URL,
        "scan_summary": {
            "total_scanned": len(live_opps),
            "go": go_count,
            "watch": watch_count,
            "watch_teaming": team_count,
            "no_go": len(no_go),
            "forecast": len(forecast_awards),
        },
        "targets": [_to_record(v) for v in go_watch],
        "no_go": [_to_record(v) for v in no_go],
        "forecast": [_to_forecast_record(a) for a in forecast_awards],
    }
    PIPELINE_JSON.write_text(json.dumps(pipeline, indent=2))
    print(f"      ✓ pipeline.json written")

    # 4. Push to GitHub Pages + send Discord
    if DRY_RUN:
        print("\n⚡ DRY RUN — skipping git push and Discord")
    else:
        print("\n[4/4] Publishing dashboard + notifying Discord...")
        _git_push()
        await notify_pipeline(go_watch, no_go, DASHBOARD_URL)

    return pipeline


async def main():
    await run_scan()


if __name__ == "__main__":
    asyncio.run(main())
