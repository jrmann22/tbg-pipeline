"""
GovTribe MCP client — calls the GovTribe HTTP MCP server directly.
Uses the Bearer token stored in .env by Claude Code.

Searches are aligned to TBG's four standing saved searches:
  • TBG — GSA PBS NCR
  • TBG — State OAQ
  • TBG — DHS CBP
  • TBG — Broad NAICS Sweep
"""
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

GOVTRIBE_MCP_URL = "https://govtribe.com/mcp"
BEARER_TOKEN = os.getenv("GOVTRIBE_BEARER_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# TBG capability codes
NAICS_CODES = ["561210", "561720", "541330", "541611", "236220"]
PSC_CODES = ["Z2AA", "S201", "R499", "R408", "C211", "Z1AA", "Z1PZ", "J041", "J045", "R425"]
ELIGIBLE_SET_ASIDES = ["Total Small Business", "Partial Small Business", "No Set-Aside Used"]
OPP_TYPES = ["Solicitation", "Pre-Solicitation", "Special Notice"]

FIELDS = [
    "govtribe_id", "name", "opportunity_type", "set_aside_type",
    "posted_date", "due_date", "govtribe_url",
    "federal_agency", "naics_category", "psc_category",
    "place_of_performance", "descriptions", "points_of_contact",
]


async def _call_tool(tool_name: str, params: dict) -> dict:
    """Send a JSON-RPC tool call to the GovTribe MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": params},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(GOVTRIBE_MCP_URL, headers=HEADERS, json=payload)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            result_text = ""
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    result_text = line[5:].strip()
            return json.loads(result_text)
        return resp.json()


def _extract_data(raw: dict) -> list[dict]:
    """Pull the data array out of an MCP tool result."""
    try:
        content = raw.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "{}")
            parsed = json.loads(text)
            return parsed.get("data", [])
    except Exception:
        pass
    return []


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def search_gsa_pbs_ncr(per_page: int = 25) -> list[dict]:
    """GSA Public Buildings Service — National Capital Region opportunities."""
    raw = await _call_tool("Search_Federal_Contract_Opportunities", {
        "query": "GSA Public Buildings Service National Capital Region",
        "naics_category_ids": NAICS_CODES,
        "set_aside_types": ELIGIBLE_SET_ASIDES,
        "opportunity_types": OPP_TYPES,
        "due_date_range": {"from": _today_iso(), "to": None},
        "fields_to_return": FIELDS,
        "per_page": per_page,
        "sort": {"key": "postedDate", "direction": "desc"},
    })
    return _extract_data(raw)


async def search_state_oaq(per_page: int = 25) -> list[dict]:
    """Department of State — Office of Acquisitions opportunities."""
    raw = await _call_tool("Search_Federal_Contract_Opportunities", {
        "query": "Department of State Office of Acquisitions facilities construction management",
        "naics_category_ids": NAICS_CODES,
        "set_aside_types": ELIGIBLE_SET_ASIDES,
        "opportunity_types": OPP_TYPES,
        "due_date_range": {"from": _today_iso(), "to": None},
        "fields_to_return": FIELDS,
        "per_page": per_page,
        "sort": {"key": "postedDate", "direction": "desc"},
    })
    return _extract_data(raw)


async def search_cbp(per_page: int = 25) -> list[dict]:
    """DHS Customs and Border Protection — facilities and construction opportunities."""
    raw = await _call_tool("Search_Federal_Contract_Opportunities", {
        "query": "Customs Border Protection CBP facilities construction maintenance",
        "naics_category_ids": NAICS_CODES,
        "set_aside_types": ELIGIBLE_SET_ASIDES,
        "opportunity_types": OPP_TYPES,
        "due_date_range": {"from": _today_iso(), "to": None},
        "fields_to_return": FIELDS,
        "per_page": per_page,
        "sort": {"key": "postedDate", "direction": "desc"},
    })
    return _extract_data(raw)


async def search_broad_naics_sweep(per_page: int = 30) -> list[dict]:
    """Broad sweep across all TBG NAICS + PSC codes — all eligible agencies."""
    raw = await _call_tool("Search_Federal_Contract_Opportunities", {
        "naics_category_ids": NAICS_CODES,
        "psc_category_ids": PSC_CODES,
        "set_aside_types": ELIGIBLE_SET_ASIDES,
        "opportunity_types": OPP_TYPES,
        "due_date_range": {"from": _today_iso(), "to": None},
        "fields_to_return": FIELDS,
        "per_page": per_page,
        "sort": {"key": "postedDate", "direction": "desc"},
    })
    return _extract_data(raw)


async def search_expiring_contracts(per_page: int = 20) -> list[dict]:
    """Forecast layer — awards in TBG NAICS codes expiring in next 90–540 days."""
    today = datetime.now(timezone.utc)
    from_date = (today + timedelta(days=90)).strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=540)).strftime("%Y-%m-%d")
    raw = await _call_tool("Search_Federal_Contract_Awards", {
        "naics_category_ids": NAICS_CODES,
        "end_date_range": {"from": from_date, "to": to_date},
        "fields_to_return": [
            "govtribe_id", "name", "award_date", "end_date",
            "dollars_obligated", "ceiling_value", "set_aside_type",
            "awardee", "funding_federal_agency", "naics_category",
            "psc_category", "govtribe_url",
        ],
        "per_page": per_page,
        "sort": {"key": "endDate", "direction": "asc"},
    })
    return _extract_data(raw)


async def fetch_all_opportunities() -> tuple[list[dict], list[dict]]:
    """
    Run all four searches in parallel, deduplicate, and return
    (live_opportunities, forecast_contracts).
    """
    import asyncio
    results = await asyncio.gather(
        search_gsa_pbs_ncr(),
        search_state_oaq(),
        search_cbp(),
        search_broad_naics_sweep(),
        search_expiring_contracts(),
        return_exceptions=True,
    )

    live_raw = []
    for r in results[:4]:
        if isinstance(r, list):
            live_raw.extend(r)

    forecast_raw = results[4] if isinstance(results[4], list) else []

    # Deduplicate live opportunities by govtribe_id
    seen: set[str] = set()
    live: list[dict] = []
    for opp in live_raw:
        oid = opp.get("govtribe_id", "")
        if oid and oid not in seen:
            seen.add(oid)
            live.append(opp)

    return live, forecast_raw
