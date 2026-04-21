"""
TBG Go/No-Go Engine — Claude API scoring with hard-filter pre-pass.

Stage 1: Python hard filters (instant, free) — automatic NO-GO if triggered.
Stage 2: Claude API scoring (intelligent) — classifies survivors GO/WATCH/WATCH_TEAMING.

Classifications: GO | WATCH | WATCH_TEAMING | NO-GO
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# TBG eligible NAICS codes
ELIGIBLE_NAICS = {"561210", "561720", "541330", "541611", "236220"}

# Hard-disqualify set-aside types (TBG ineligible)
EXCLUDED_SET_ASIDES = {
    "8(a) Sole Source",
    "Competitive 8(a)",
    "HUBZone Sole Source",
    "HUBZone",
    "Service-Disabled Veteran-Owned Small Business Sole Source",
    "Service-Disabled Veteran-Owned Small Business",
    "Veteran Sole Source",
    "Veteran-Owned Small Business",
    "Economically Disadvantaged Woman-Owned Small Business",
    "Woman-Owned Small Business Sole Source",
    "Woman-Owned Small Business",
}

CLAUDE_SYSTEM_PROMPT = """You are a federal business development analyst for The Blackshear Group, LLC (TBG),
a pre-revenue small business federal contractor based in Springfield, VA.

UEI: ZNTMDN4Y3NN8 | CAGE: 885Z9 | SAM: Active | Entity type: Small Business

---

COMPANY CONTEXT

TBG is in its first year of federal market entry. The firm has no federal prime
contract history, no existing vehicle registrations, and no federal past performance
on record. The principal is Justin Mann, PE, PMP, CCM — a licensed Professional
Engineer, Project Management Professional, and Certified Construction Manager with
commercial project experience in facilities, construction, and program management.

TBG's near-term strategy targets SAP-range awards (under $250K) and Total Small
Business set-asides while pursuing a GSA MAS schedule application. No surety bond
relationship is currently established — bonding-required awards require a teaming
partner, not automatic disqualification.

CRITICAL: The SBA 8(a) program is currently suspended. 8(a)-only opportunities
are already filtered out before reaching you.

---

TBG CAPABILITY CODES

NAICS: 561210, 561720, 541330, 541611, 236220
PSC: Z2AA, S201, R499, R408, C211, Z1AA, Z1PZ, J041, J045, R425

---

PRIORITY AGENCIES (in order)

1. GSA Public Buildings Service — National Capital Region (PBS NCR)
2. Department of State — Office of Acquisitions (OAQ)
3. DHS Customs & Border Protection (CBP)
Other federal civilian: evaluate on merit. DoD: lower priority.

---

SCORING CRITERIA (apply only to opportunities that passed hard filters)

NAICS CODE MATCH (max 20)
  Primary NAICS exact match: 20 | Secondary NAICS: 12 | PSC match only: 8

SET-ASIDE TYPE (max 20)
  Total Small Business: 20 | Small Business: 18 | Open competition: 10

AWARD VALUE (max 15)
  $25K–$250K: 15 | $250K–$1M: 10 | $1M–$5M: 6 | $5M–$10M: 3 | else: 0

AGENCY PRIORITY (max 15)
  GSA PBS NCR: 15 | State OAQ: 13 | DHS CBP: 13 | Other federal civilian: 8 | DoD: 5

PAST PERFORMANCE REQUIREMENT (max 10)
  None required: 10 | "Relevant experience" accepted: 8 | 1-2 federal refs: 4

GEOGRAPHIC ALIGNMENT (max 10)
  DC/MD/VA: 10 | CONUS multi-site: 6 | Single site outside Mid-Atlantic: 3

RESPONSE TIME (max 5)
  21+ days: 5 | 15-20 days: 3 | 10-14 days: 1 | under 10 days: 0

INCUMBENT MODIFIER
  Known incumbent with 2+ consecutive awards: subtract 5 from total score

---

BONDING FLAG LOGIC

If bonding is required AND it is the ONLY reason the opportunity would not score GO:
  → Classify as GO or WATCH per score, set teaming_flag: true
  → Do NOT classify as NO-GO solely due to bonding

---

CLASSIFICATION THRESHOLDS

GO: score >= 60
WATCH: score 35-59
WATCH_TEAMING: any score, bonding is the only barrier
NO-GO: score < 35

---

OUTPUT FORMAT

Return a JSON array. One object per opportunity. No prose, no markdown, only the array.

[
  {
    "opportunity_id": "string",
    "classification": "GO" | "WATCH" | "WATCH_TEAMING" | "NO-GO",
    "score": integer,
    "score_breakdown": {
      "naics_match": integer,
      "set_aside": integer,
      "award_value": integer,
      "agency_priority": integer,
      "past_performance": integer,
      "geographic": integer,
      "response_time": integer,
      "incumbent_modifier": integer
    },
    "bonding_required": boolean,
    "teaming_flag": boolean,
    "priority_agency": boolean,
    "reason_summary": "2-3 sentences explaining the classification",
    "recommended_action": "One specific next step for Justin"
  }
]"""


@dataclass
class Verdict:
    opportunity_id: str
    name: str
    verdict: str            # GO | WATCH | WATCH_TEAMING | NO-GO
    score: int
    kill_reason: Optional[str]
    wedge_signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bonding_required: bool = False
    teaming_flag: bool = False
    priority_agency: bool = False
    reason_summary: str = ""
    recommended_action: str = ""
    score_breakdown: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def is_go(self) -> bool:
        return self.verdict in ("GO", "WATCH", "WATCH_TEAMING")


def _hard_filter(opp: dict) -> Optional[str]:
    """
    Apply automatic NO-GO rules. Returns kill reason string or None if clean.
    These checks run before Claude API — instant and free.
    """
    naics = opp.get("naics_category", {})
    naics_code = str(naics.get("govtribe_id", "")) if isinstance(naics, dict) else ""
    naics_code = naics_code.replace("-N", "")

    set_aside = opp.get("set_aside_type", "") or ""
    due = opp.get("due_date", "") or ""

    # 1. Ineligible set-aside
    if set_aside in EXCLUDED_SET_ASIDES:
        return f"Ineligible set-aside: {set_aside}"

    # 2. NAICS not in TBG's codes
    if naics_code and naics_code not in ELIGIBLE_NAICS:
        return f"NAICS {naics_code} not in TBG portfolio"

    # 3. Response deadline < 10 days
    if due:
        try:
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            days_left = (due_dt - datetime.now(timezone.utc)).days
            if days_left < 10:
                return f"Deadline too close: {days_left} days remaining"
        except Exception:
            pass

    # 4. Award value > $10M (checked from description keywords — best effort)
    # Claude will catch specific dollar mentions in descriptions

    return None


def _build_batch_message(opportunities: list[dict]) -> str:
    """Build the user message for a batch Claude API call."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = []
    for opp in opportunities:
        agency = opp.get("federal_agency", {})
        naics = opp.get("naics_category", {})
        psc = opp.get("psc_category", {})
        pop = opp.get("place_of_performance", {})
        descs = opp.get("descriptions", [])
        desc_text = " ".join(
            d.get("body", "")[:300] for d in descs if isinstance(d, dict)
        )[:600]

        items.append({
            "opportunity_id": opp.get("govtribe_id", ""),
            "title": opp.get("name", ""),
            "agency": agency.get("name", "") if isinstance(agency, dict) else "",
            "naics": naics.get("govtribe_id", "") if isinstance(naics, dict) else "",
            "psc": psc.get("govtribe_id", "") if isinstance(psc, dict) else "",
            "set_aside": opp.get("set_aside_type", ""),
            "opportunity_type": opp.get("opportunity_type", ""),
            "due_date": (opp.get("due_date", "") or "")[:10],
            "posted_date": (opp.get("posted_date", "") or "")[:10],
            "location": pop.get("city", "") if isinstance(pop, dict) else "",
            "description_excerpt": desc_text,
            "today_date": today,
        })

    return (
        f"Evaluate these {len(items)} federal opportunities for TBG. "
        "Apply your classification framework exactly. "
        "Return only the JSON array — no prose.\n\n"
        + json.dumps(items, indent=2)
    )


def _claude_classify(opportunities: list[dict]) -> dict[str, dict]:
    """Call Claude API to score and classify opportunities. Returns dict keyed by opportunity_id."""
    if not ANTHROPIC_API_KEY:
        print("  ⚠ ANTHROPIC_API_KEY not set — using fallback WATCH classification")
        return {}

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=CLAUDE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_batch_message(opportunities)}],
        )

        raw_text = message.content[0].text.strip()
        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        results = json.loads(raw_text.strip())
        return {r["opportunity_id"]: r for r in results if "opportunity_id" in r}

    except Exception as e:
        print(f"  ⚠ Claude API error: {e} — falling back to WATCH classification")
        return {}


def evaluate_batch(opportunities: list[dict]) -> tuple[list[Verdict], list[Verdict]]:
    """
    Evaluate all opportunities. Returns (go_watch_list, no_go_list).
    Stage 1: hard filters (Python). Stage 2: Claude API scoring.
    """
    hard_no_go: list[Verdict] = []
    survivors: list[dict] = []

    for opp in opportunities:
        kill = _hard_filter(opp)
        if kill:
            hard_no_go.append(Verdict(
                opportunity_id=opp.get("govtribe_id", ""),
                name=opp.get("name", "Unnamed"),
                verdict="NO-GO",
                score=0,
                kill_reason=kill,
                raw=opp,
            ))
        else:
            survivors.append(opp)

    if not survivors:
        return [], hard_no_go

    # Batch Claude scoring — one API call for all survivors
    print(f"  Sending {len(survivors)} survivors to Claude for scoring...")
    claude_results = _claude_classify(survivors)

    go_watch: list[Verdict] = []
    soft_no_go: list[Verdict] = []

    for opp in survivors:
        oid = opp.get("govtribe_id", "")
        name = opp.get("name", "Unnamed")
        result = claude_results.get(oid, {})

        if not result:
            # Fallback: no Claude result → WATCH
            go_watch.append(Verdict(
                opportunity_id=oid, name=name,
                verdict="WATCH", score=35,
                kill_reason=None,
                reason_summary="Claude scoring unavailable — manual review required.",
                recommended_action="Review opportunity manually and apply Go/No-Go criteria.",
                raw=opp,
            ))
            continue

        classification = result.get("classification", "NO-GO")
        score = result.get("score", 0)

        v = Verdict(
            opportunity_id=oid,
            name=name,
            verdict=classification,
            score=score,
            kill_reason=None if classification != "NO-GO" else result.get("reason_summary", ""),
            bonding_required=result.get("bonding_required", False),
            teaming_flag=result.get("teaming_flag", False),
            priority_agency=result.get("priority_agency", False),
            reason_summary=result.get("reason_summary", ""),
            recommended_action=result.get("recommended_action", ""),
            score_breakdown=result.get("score_breakdown", {}),
            raw=opp,
        )

        if classification == "NO-GO":
            soft_no_go.append(v)
        else:
            go_watch.append(v)

    go_watch_sorted = sorted(go_watch, key=lambda v: -v.score)
    all_no_go = hard_no_go + soft_no_go

    return go_watch_sorted, all_no_go


# Legacy alias so discord_notifier import still works
def run_pipeline(opportunities: list[dict]) -> tuple[list[Verdict], list[Verdict]]:
    return evaluate_batch(opportunities)
