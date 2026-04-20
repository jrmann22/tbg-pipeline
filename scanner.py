"""
TBG Pipeline Scanner — main orchestration script.

Usage:
    python scanner.py              # run once immediately
    python scanner.py --loop       # run on schedule (SCAN_INTERVAL_HOURS from .env)
    python scanner.py --dry-run    # run without posting to Discord

Schedule with Windows Task Scheduler:
    Action: python d:\QuantDesk\GovTribe\scanner.py
    Trigger: Daily at 07:00
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from discord_notifier import notify_pipeline
from go_no_go import run_pipeline
from govtribe_client import (
    search_fta_opportunities,
    search_all_fed_opportunities,
    search_fta_awards,
    search_incumbent_vendors,
)

load_dotenv()

PIPELINE_JSON = Path(__file__).parent / "pipeline.json"
DRY_RUN = "--dry-run" in sys.argv
LOOP_MODE = "--loop" in sys.argv


def _to_pipeline_record(v) -> dict:
    """Convert a Verdict into a dashboard-friendly JSON record."""
    opp = v.raw
    due = opp.get("due_date", "")
    posted = opp.get("posted_date", "")
    agency = opp.get("federal_agency", {})
    naics = opp.get("naics_category", {})
    return {
        "id": v.opportunity_id,
        "name": v.name,
        "verdict": v.verdict,
        "score": v.score,
        "kill_reason": v.kill_reason,
        "wedge_signals": v.wedge_signals,
        "warnings": v.warnings,
        "agency": agency.get("name", "") if isinstance(agency, dict) else "",
        "agency_url": agency.get("govtribe_url", "") if isinstance(agency, dict) else "",
        "opportunity_type": opp.get("opportunity_type", ""),
        "set_aside_type": opp.get("set_aside_type", ""),
        "posted_date": posted[:10] if posted else "",
        "due_date": due[:10] if due else "",
        "govtribe_url": opp.get("govtribe_url", ""),
        "naics": naics.get("name", "") if isinstance(naics, dict) else "",
    }


async def run_scan() -> dict:
    """Execute a full pipeline scan and return structured results."""
    print(f"\n{'='*60}")
    print(f"TBG PIPELINE SCAN — {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(f"{'='*60}")

    # ── 1. Pull opportunities ────────────────────────────────
    print("\n[1/4] Fetching FTA opportunities...")
    fta_opps = await search_fta_opportunities(per_page=25)
    print(f"      {len(fta_opps)} FTA opportunities found")

    print("[2/4] Fetching broader federal advisory opportunities...")
    fed_opps = await search_all_fed_opportunities(per_page=30)
    print(f"      {len(fed_opps)} federal advisory opportunities found")

    # Deduplicate by govtribe_id
    seen = set()
    all_opps = []
    for opp in fta_opps + fed_opps:
        oid = opp.get("govtribe_id", "")
        if oid not in seen:
            seen.add(oid)
            all_opps.append(opp)
    print(f"      {len(all_opps)} unique opportunities after dedup")

    # ── 2. Run Go/No-Go filter ───────────────────────────────
    print("\n[3/4] Running Go/No-Go analysis...")
    go_list, no_go_list = run_pipeline(all_opps)
    print(f"      🟢 GO: {len([v for v in go_list if v.verdict == 'GO'])}")
    print(f"      🟡 CONDITIONAL: {len([v for v in go_list if v.verdict == 'CONDITIONAL GO'])}")
    print(f"      🔴 NO-GO: {len(no_go_list)}")

    # Print GO targets to console
    if go_list:
        print("\n── GO TARGETS ──")
        for v in go_list:
            opp = v.raw
            due = opp.get("due_date", "")[:10]
            print(f"  [{v.verdict:14s}] score={v.score:3d}  due={due or 'open':10s}  {v.name[:70]}")

    # ── 3. Fetch benchmark data ──────────────────────────────
    print("\n[4/4] Fetching benchmark awards + incumbent vendors...")
    try:
        awards = await search_fta_awards(per_page=8)
        vendors = await search_incumbent_vendors()
    except Exception as e:
        print(f"      Benchmark fetch warning: {e}")
        awards, vendors = [], []
    print(f"      {len(awards)} benchmark awards, {len(vendors)} incumbent vendors")

    # ── 4. Write pipeline.json ───────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    pipeline = {
        "generated_at": now_iso,
        "scan_summary": {
            "total_scanned": len(all_opps),
            "go": len([v for v in go_list if v.verdict == "GO"]),
            "conditional": len([v for v in go_list if v.verdict == "CONDITIONAL GO"]),
            "no_go": len(no_go_list),
        },
        "go_targets": [_to_pipeline_record(v) for v in go_list],
        "no_go": [_to_pipeline_record(v) for v in no_go_list],
        "benchmark_awards": [
            {
                "name": a.get("name", ""),
                "award_date": (a.get("award_date", "") or "")[:10],
                "dollars_obligated": a.get("dollars_obligated", 0),
                "ceiling_value": a.get("ceiling_value", 0),
                "awardee": a.get("awardee", {}).get("name", "") if isinstance(a.get("awardee"), dict) else "",
                "govtribe_url": a.get("govtribe_url", ""),
            }
            for a in awards
        ],
        "incumbent_vendors": [
            {
                "name": v.get("name", ""),
                "uei": v.get("uei", ""),
                "business_types": v.get("business_types", [])[:3],
                "awarded": v.get("awarded_federal_contract_award", False),
                "govtribe_url": v.get("govtribe_url", ""),
            }
            for v in vendors
        ],
    }

    PIPELINE_JSON.write_text(json.dumps(pipeline, indent=2))
    print(f"\n✓ pipeline.json written → {PIPELINE_JSON}")

    # ── 5. Discord notification ──────────────────────────────
    if DRY_RUN:
        print("⚡ DRY RUN — skipping Discord")
    else:
        print("\n[Discord] Posting notifications...")
        await notify_pipeline(go_list, no_go_list)

    return pipeline


async def main():
    import os
    if LOOP_MODE:
        interval_hours = int(os.getenv("SCAN_INTERVAL_HOURS", "24"))
        print(f"Loop mode: scanning every {interval_hours}h. Ctrl+C to stop.")
        while True:
            await run_scan()
            print(f"\nSleeping {interval_hours}h until next scan...")
            await asyncio.sleep(interval_hours * 3600)
    else:
        await run_scan()


if __name__ == "__main__":
    asyncio.run(main())
