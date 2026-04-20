"""
GovTribe MCP client — calls the GovTribe HTTP MCP server directly.
Uses the same Bearer token stored by Claude Code.
"""
import asyncio
import json
import os
from typing import Any

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


async def _call_tool(tool_name: str, params: dict) -> dict:
    """Send a JSON-RPC tool call to the GovTribe MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": params,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(GOVTRIBE_MCP_URL, headers=HEADERS, json=payload)
        resp.raise_for_status()
        # MCP may return SSE or plain JSON
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # Parse SSE — collect data lines
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


async def search_fta_opportunities(
    query: str = "project management oversight advisory transit",
    per_page: int = 25,
) -> list[dict]:
    """Return open FTA contract opportunities."""
    raw = await _call_tool(
        "Search_Federal_Contract_Opportunities",
        {
            "query": query,
            "search_mode": "semantic",
            "federal_agency_ids": ["6900|6955-A"],  # FTA
            "fields_to_return": [
                "govtribe_id", "name", "opportunity_type", "set_aside_type",
                "posted_date", "due_date", "govtribe_url",
                "federal_agency", "naics_category", "descriptions",
            ],
            "per_page": per_page,
            "sort": {"key": "postedDate", "direction": "desc"},
        },
    )
    return _extract_data(raw)


async def search_all_fed_opportunities(
    query: str = "program management oversight advisory engineering",
    per_page: int = 50,
) -> list[dict]:
    """Broader search — all federal agencies (used for non-FTA GO scan)."""
    raw = await _call_tool(
        "Search_Federal_Contract_Opportunities",
        {
            "query": query,
            "search_mode": "semantic",
            "opportunity_types": ["Solicitation", "Pre-Solicitation"],
            "fields_to_return": [
                "govtribe_id", "name", "opportunity_type", "set_aside_type",
                "posted_date", "due_date", "govtribe_url",
                "federal_agency", "naics_category", "descriptions",
            ],
            "per_page": per_page,
            "sort": {"key": "postedDate", "direction": "desc"},
        },
    )
    return _extract_data(raw)


async def search_fta_awards(per_page: int = 10) -> list[dict]:
    """Return recent FTA PMO/advisory awards for benchmarking."""
    raw = await _call_tool(
        "Search_Federal_Contract_Awards",
        {
            "query": "project management oversight advisory",
            "search_mode": "semantic",
            "funding_federal_agency_ids": ["6900|6955-A"],
            "fields_to_return": [
                "govtribe_id", "name", "award_date", "dollars_obligated",
                "ceiling_value", "contract_type", "awardee", "govtribe_url",
            ],
            "per_page": per_page,
            "sort": {"key": "financialStats.dollarsObligated", "direction": "desc"},
        },
    )
    return _extract_data(raw)


async def search_incumbent_vendors(query: str = "transit program management oversight") -> list[dict]:
    """Return vendors active in the FTA oversight space."""
    raw = await _call_tool(
        "Search_Vendors",
        {
            "query": query,
            "search_mode": "semantic",
            "fields_to_return": [
                "govtribe_id", "name", "uei", "address",
                "business_types", "sba_certifications",
                "govtribe_url", "awarded_federal_contract_award",
            ],
            "per_page": 10,
        },
    )
    return _extract_data(raw)
