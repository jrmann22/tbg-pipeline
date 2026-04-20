"""
TBG Go/No-Go Engine — Justin Mann, PE, PMP, CCM
Applies firm operating constraints to every opportunity automatically.
"""
from dataclasses import dataclass, field
from typing import Optional


# ── Constraint Keywords ────────────────────────────────────

# Automatic NO-GO: blue-collar / self-perform execution
EXECUTION_BAN_KEYWORDS = [
    "construction", "demolition", "excavation", "grading", "paving",
    "concrete placement", "structural steel erection", "facility maintenance",
    "janitorial", "custodial", "landscaping", "mechanical installation",
    "plumbing", "electrical installation", "hvac installation",
    "painting", "roofing", "flooring", "carpentry", "masonry",
    "welding", "pipefitting", "ironworker", "operating engineer",
    "laborer", "teamster",
]

# Automatic NO-GO: bonding required
BONDING_KEYWORDS = [
    "performance bond", "payment bond", "surety bond", "bid bond",
    "miller act", "little miller act", "bonding requirement",
    "100% performance and payment", "bonding capacity",
]

# Automatic NO-GO: OCI — WHS / Pentagon
OCI_KEYWORDS = [
    "washington headquarters services", "whs", "pentagon",
    "defense media activity", "osd", "office of the secretary of defense",
    "joint chiefs", "djia", "defense information systems agency",
    "defense logistics agency",  # add more if needed
]

# Strong GO signals for Justin's wheelhouse
GO_SIGNALS = [
    "project management oversight", "pmo", "program management oversight",
    "construction management", "cm/gc", "independent oversight",
    "schedule audit", "earned value", "pmis", "evm",
    "federal transit", "fta", "transit authority",
    "financial management oversight", "fmo",
    "safety management inspection", "smi",
    "procurement system review", "psr",
    "technical advisory", "owner's representative",
    "pe ", "professional engineer", "ccm", "certified construction manager",
    "schedule delay analysis", "claims analysis",
    "capital program", "mega project", "heavy rail", "light rail",
]

# Weak GO — advisory/consulting broadly
ADVISORY_SIGNALS = [
    "advisory", "consulting", "management consulting",
    "program management", "project controls",
    "technical assistance", "oversight", "audit",
    "541611", "541330", "541990",
]


@dataclass
class Verdict:
    opportunity_id: str
    name: str
    verdict: str                    # "GO", "NO-GO", "CONDITIONAL GO"
    score: int                      # 0-100 signal strength
    kill_reason: Optional[str]      # populated on NO-GO
    wedge_signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def is_go(self) -> bool:
        return self.verdict in ("GO", "CONDITIONAL GO")


def _text(opp: dict) -> str:
    """Flatten all searchable text from an opportunity record."""
    parts = [
        opp.get("name", ""),
        opp.get("set_aside_type", ""),
        opp.get("opportunity_type", ""),
    ]
    descs = opp.get("descriptions", [])
    if isinstance(descs, list):
        parts.extend(d.get("body", "") for d in descs if isinstance(d, dict))
    agency = opp.get("federal_agency", {})
    if isinstance(agency, dict):
        parts.append(agency.get("name", ""))
    naics = opp.get("naics_category", {})
    if isinstance(naics, dict):
        parts.append(naics.get("name", ""))
        parts.append(str(naics.get("govtribe_id", "")))
    return " ".join(parts).lower()


def evaluate(opp: dict) -> Verdict:
    """Run a single opportunity through the Go/No-Go filter."""
    opp_id = opp.get("govtribe_id", "unknown")
    name = opp.get("name", "Unnamed Opportunity")
    text = _text(opp)

    # ── KILL SWITCHES (automatic NO-GO) ──────────────────────

    # 1. OCI check — WHS / Pentagon
    for kw in OCI_KEYWORDS:
        if kw in text:
            return Verdict(
                opportunity_id=opp_id, name=name,
                verdict="NO-GO", score=0,
                kill_reason=f"OCI VIOLATION — WHS/Pentagon keyword detected: '{kw}'. "
                            "Firm currently executes civil work inside WHS perimeter.",
                raw=opp,
            )

    # 2. Execution ban — blue-collar self-perform
    execution_hits = [kw for kw in EXECUTION_BAN_KEYWORDS if kw in text]
    if len(execution_hits) >= 2:
        return Verdict(
            opportunity_id=opp_id, name=name,
            verdict="NO-GO", score=0,
            kill_reason=f"EXECUTION BAN — Blue-collar self-perform indicators: "
                        f"{', '.join(execution_hits[:3])}. Firm does not turn wrenches.",
            raw=opp,
        )

    # 3. Bonding requirement
    bonding_hits = [kw for kw in BONDING_KEYWORDS if kw in text]
    if bonding_hits:
        return Verdict(
            opportunity_id=opp_id, name=name,
            verdict="NO-GO", score=0,
            kill_reason=f"BONDING BAN — Surety bond language detected: "
                        f"'{bonding_hits[0]}'. Firm has zero native bonding capacity.",
            raw=opp,
        )

    # ── SCORING (GO signals) ─────────────────────────────────

    wedge_signals = [kw for kw in GO_SIGNALS if kw in text]
    advisory_signals = [kw for kw in ADVISORY_SIGNALS if kw in text]
    all_signals = wedge_signals + advisory_signals

    score = min(100, len(wedge_signals) * 15 + len(advisory_signals) * 5)

    warnings = []

    # Conditional warning: single execution-ban keyword (not enough to kill)
    if execution_hits:
        warnings.append(
            f"Contains execution language '{execution_hits[0]}' — verify scope "
            "is advisory/oversight only before bidding."
        )

    # ── VERDICT ──────────────────────────────────────────────

    if score >= 30 or len(wedge_signals) >= 2:
        verdict = "GO"
    elif score >= 10 or len(all_signals) >= 1:
        verdict = "CONDITIONAL GO"
    else:
        verdict = "NO-GO"
        return Verdict(
            opportunity_id=opp_id, name=name,
            verdict="NO-GO", score=score,
            kill_reason="WEAK SIGNAL — No advisory/oversight/PM indicators found. "
                        "Not in TBG's lane.",
            raw=opp,
        )

    return Verdict(
        opportunity_id=opp_id, name=name,
        verdict=verdict, score=score,
        kill_reason=None,
        wedge_signals=wedge_signals[:5],
        warnings=warnings,
        raw=opp,
    )


def run_pipeline(opportunities: list[dict]) -> tuple[list[Verdict], list[Verdict]]:
    """Evaluate all opportunities. Returns (go_list, no_go_list)."""
    verdicts = [evaluate(opp) for opp in opportunities]
    go = sorted([v for v in verdicts if v.is_go], key=lambda v: -v.score)
    no_go = [v for v in verdicts if not v.is_go]
    return go, no_go
