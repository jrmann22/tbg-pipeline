"""Score all opportunities and write pipeline.json."""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(r'C:\Users\jrman\.claude\projects\d--QuantDesk-GovTribe\b272b280-e072-4809-ae1e-82f623cae8a4\tool-results')
TODAY = datetime.now(timezone.utc)

ELIGIBLE_NAICS = {'561210', '561720', '541330', '541611', '236220'}
PRIMARY_NAICS = {'561210', '561720', '236220'}
EXCLUDED_SA = {
    '8(a) Sole Source', 'Competitive 8(a)', 'HUBZone Sole Source', 'HUBZone',
    'Service-Disabled Veteran-Owned Small Business Sole Source',
    'Service-Disabled Veteran-Owned Small Business', 'Veteran Sole Source',
    'Veteran-Owned Small Business', 'Economically Disadvantaged Woman-Owned Small Business',
    'Woman-Owned Small Business Sole Source', 'Woman-Owned Small Business',
}

# Mandatory site visit / pre-proposal conference keywords
MANDATORY_KEYWORDS = [
    r'mandatory.{0,30}(site visit|pre-?proposal|pre-?bid|conference|inspection|attendance)',
    r'(site visit|pre-?proposal|pre-?bid|conference).{0,30}mandatory',
    r'attendance.{0,20}(required|mandatory)',
    r'(required|mandatory).{0,20}attendance',
    r'must attend.{0,30}(site|conference|visit|inspection)',
]

# Q&A / RFI / questions deadline keywords
QA_RFI_KEYWORDS = [
    r'questions?.{0,30}(due|deadline|must be (submitted|received)|submit(ted)? by)',
    r'(due|deadline).{0,20}questions?',
    r'written questions?.{0,40}(by|before|no later than)',
    r'(rfq?|rfi).{0,30}(due|deadline|response)',
    r'inquir(y|ies).{0,30}(due|deadline|by)',
    r'submit.{0,30}questions?.{0,30}(by|before|no later than)',
    r'last day.{0,20}(for |to submit )?questions?',
    r'q&a.{0,30}(due|closes?|deadline|date)',
]

# Date patterns to extract from description text
DATE_PATTERNS = [
    r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',
    r'\d{1,2}/\d{1,2}/\d{4}',
    r'\d{4}-\d{2}-\d{2}',
]

MONTH_MAP = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def extract_description_text(descriptions: list) -> str:
    """Pull plain text from GovTribe's udiff description format."""
    if not descriptions:
        return ''
    parts = []
    for d in descriptions:
        if not isinstance(d, dict):
            continue
        udiff = d.get('udiff', '')
        # Extract lines added (+ prefix) — these are the actual content lines
        for line in udiff.splitlines():
            if line.startswith('+') and not line.startswith('+++'):
                parts.append(line[1:].strip())
    return ' '.join(parts)


def parse_date(date_str: str):
    """Parse a date string into a datetime, return None on failure."""
    date_str = date_str.strip().rstrip(',')
    formats = ['%B %d %Y', '%B %d, %Y', '%m/%d/%Y', '%Y-%m-%d', '%m %d %Y', '%m %d, %Y']
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def check_mandatory_site_visit(desc_text: str):
    """
    Detect mandatory site visit / pre-proposal conference in description text.
    Returns (is_mandatory: bool, visit_date: datetime|None, days_until: int|None)
    """
    text_lower = desc_text.lower()

    is_mandatory = any(
        re.search(pattern, text_lower, re.IGNORECASE)
        for pattern in MANDATORY_KEYWORDS
    )

    if not is_mandatory:
        return False, None, None

    # Try to extract the closest date after a mandatory keyword hit
    # Search in a window around each keyword match
    visit_date = None
    for pattern in MANDATORY_KEYWORDS:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if not match:
            continue
        # Look for dates within 300 characters of the keyword
        window_start = max(0, match.start() - 50)
        window_end = min(len(desc_text), match.end() + 300)
        window = desc_text[window_start:window_end]

        for dp in DATE_PATTERNS:
            dates = re.findall(dp, window, re.IGNORECASE)
            for ds in dates:
                # Normalize month names
                for month, num in MONTH_MAP.items():
                    ds = re.sub(month, str(num), ds, flags=re.IGNORECASE)
                dt = parse_date(ds)
                if dt:
                    # Take the earliest future date, or most recent past date
                    if visit_date is None:
                        visit_date = dt
                    elif dt < TODAY and (visit_date is None or dt > visit_date):
                        visit_date = dt  # most recent past
                    elif dt >= TODAY and (visit_date is None or dt < visit_date):
                        visit_date = dt  # nearest future
            if visit_date:
                break
        if visit_date:
            break

    days_until = None
    if visit_date:
        days_until = (visit_date - TODAY).days

    return True, visit_date, days_until


# Broader site visit / pre-proposal conference anchor keywords (no "mandatory" required)
SITE_VISIT_ANCHORS = [
    r'(pre-?proposal|pre-?bid)\s+(conference|meeting|site)',
    r'site\s+visit\s+(date|scheduled|will be|is)',
    r'site\s+inspection\s+(date|scheduled)',
    r'mandatory.{0,30}(site visit|pre-?proposal|pre-?bid|conference|attendance)',
    r'(site visit|pre-?proposal|pre-?bid|conference).{0,30}mandatory',
    r'attendance.{0,20}(required|mandatory)',
    r'(required|mandatory).{0,20}attendance',
    r'must attend.{0,30}(site|conference|visit|inspection)',
]


def _find_nearest_date(desc_text: str, match_end: int, match_start: int) -> 'datetime | None':
    """Extract the nearest date in a ±300-char window around a keyword match."""
    window_start = max(0, match_start - 50)
    window_end = min(len(desc_text), match_end + 300)
    window = desc_text[window_start:window_end]
    for dp in DATE_PATTERNS:
        dates = re.findall(dp, window, re.IGNORECASE)
        for ds in dates:
            for month, num in MONTH_MAP.items():
                ds = re.sub(month, str(num), ds, flags=re.IGNORECASE)
            dt = parse_date(ds)
            if dt:
                return dt
    return None


def find_site_visit_date(desc_text: str) -> 'datetime | None':
    """Return the date of any site visit / pre-proposal conference mentioned."""
    text_lower = desc_text.lower()
    for pattern in SITE_VISIT_ANCHORS:
        m = re.search(pattern, text_lower, re.IGNORECASE)
        if m:
            dt = _find_nearest_date(desc_text, m.end(), m.start())
            if dt:
                return dt
    return None


def check_qa_rfi_date(desc_text: str):
    """
    Detect Q&A / RFI / questions deadline in description text.
    Returns datetime | None.
    """
    text_lower = desc_text.lower()
    for pattern in QA_RFI_KEYWORDS:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if not match:
            continue
        dt = _find_nearest_date(desc_text, match.end(), match.start())
        if dt:
            return dt
    return None


def site_visit_kill_reason(visit_date, days_until: int) -> str | None:
    """Return a kill reason if site visit makes the opportunity unbiddable, else None."""
    if days_until is None:
        # Mandatory site visit found but couldn't extract date — flag as Watch warning
        return None
    if days_until < 0:
        return f'Mandatory site visit/conference already occurred ({abs(days_until)}d ago) — unbiddable'
    if days_until < 3:
        return f'Mandatory site visit/conference in {days_until}d — insufficient time to attend'
    return None


def days_left(due_str):
    if not due_str:
        return 999
    try:
        if 'T' not in due_str:
            due_str += 'T00:00:00+00:00'
        d = datetime.fromisoformat(due_str.replace('Z', '+00:00'))
        return (d - TODAY).days
    except Exception:
        return 999


def fmt_iso(dt) -> str:
    """Return YYYY-MM-DD string for a datetime, or empty string."""
    if dt is None:
        return ''
    return dt.strftime('%Y-%m-%d')


def score_opp(o):
    """Returns (score, kill_reason, bonding_required, site_visit_warning, site_visit_date, qa_rfi_date)."""
    sa = o.get('set_aside', '')
    naics = o.get('naics', '')
    loc = (o.get('location', '') or '').lower()
    agency = (o.get('agency', '') or '').lower()
    due = o.get('due', '')
    desc = o.get('desc_text', '')
    dl = days_left(due)

    # --- Hard filters ---
    if sa in EXCLUDED_SA:
        return None, f'Ineligible set-aside: {sa}', False, None, '', ''

    if naics and naics not in ELIGIBLE_NAICS:
        return None, f'NAICS {naics} not in TBG codes', False, None, '', ''

    if dl < 10:
        return None, f'Deadline too close: {dl} days remaining', False, None, '', ''

    # --- Mandatory site visit check ---
    site_visit_warning = None
    sv_date_str = ''
    qa_date_str = ''
    if desc:
        # Broad date tracking (all site visits / pre-proposal conferences)
        sv_dt = find_site_visit_date(desc)
        sv_date_str = fmt_iso(sv_dt)

        # Mandatory kill / warning check
        is_mandatory, visit_date, days_until = check_mandatory_site_visit(desc)
        if is_mandatory:
            # Use the mandatory-specific date if available, else fall back to broad one
            effective_date = visit_date or sv_dt
            effective_days = days_until
            if effective_days is None and effective_date:
                effective_days = (effective_date - TODAY).days
            kill = site_visit_kill_reason(effective_date, effective_days)
            if kill:
                return None, kill, False, None, fmt_iso(effective_date), ''
            if effective_days is not None and effective_days <= 7:
                site_visit_warning = f'Mandatory site visit in {effective_days}d — register immediately'
            elif effective_days is None:
                site_visit_warning = 'Mandatory site visit/conference required — verify date before bidding'
            else:
                site_visit_warning = f'Mandatory site visit in {effective_days}d'

        qa_dt = check_qa_rfi_date(desc)
        qa_date_str = fmt_iso(qa_dt)

    # --- Scoring ---
    nm = 20 if naics in PRIMARY_NAICS else 12 if naics in ELIGIBLE_NAICS else 8

    sa_score = {'Total Small Business': 20, 'Partial Small Business': 15, 'No Set-Aside Used': 10}.get(sa, 0)

    if 'gsa' in agency or 'public buildings' in agency or 'general services' in agency:
        ap = 15
    elif 'state' in agency and any(x in agency for x in ['acquisition', 'bureau', 'oaq']):
        ap = 13
    elif 'customs' in agency or 'border protection' in agency:
        ap = 13
    elif any(x in agency for x in ['army', 'navy', 'air force', 'marine', 'defense', 'pentagon', 'usmc']):
        ap = 5
    elif agency:
        ap = 8
    else:
        ap = 10

    geo_score = 10 if any(x in loc for x in [' va', 'virginia', 'maryland', ' md', 'washington', 'd.c.', 'dc']) \
        else 6 if (not loc or 'usa' in loc) else 3

    rt = 5 if dl >= 21 else 3 if dl >= 15 else 1

    score = nm + sa_score + ap + 8 + geo_score + rt  # 8 = default past performance score

    bonding = naics == '236220' or any(x in (o.get('psc', '') or '') for x in ['Z1', 'Z2', 'Y1'])

    return score, None, bonding, site_visit_warning, sv_date_str, qa_date_str


def forecast_stage(end_date_str):
    if not end_date_str:
        return 'MONITOR'
    try:
        ed = datetime.fromisoformat(end_date_str + 'T00:00:00+00:00')
        months_out = (ed - TODAY).days / 30
        return 'MONITOR' if months_out > 12 else 'OUTREACH' if months_out > 9 else 'ACTIVE_PURSUIT'
    except Exception:
        return 'MONITOR'


def action_date(end_date_str):
    if not end_date_str:
        return ''
    try:
        ed = datetime.fromisoformat(end_date_str + 'T00:00:00+00:00')
        return (ed - timedelta(days=270)).strftime('%Y-%m-%d')
    except Exception:
        return ''


# ── Load and parse raw opportunity files ──────────────────────────────────────

all_opps = []
seen = set()

for f in sorted(BASE.glob('mcp-govtribe-Search_Federal_Contract_Opportunities-*.txt')):
    try:
        data = json.loads(f.read_text(encoding='utf-8'))
        for opp in data.get('data', []):
            oid = opp.get('govtribe_id', '')
            if not oid or oid in seen:
                continue
            seen.add(oid)
            agency = opp.get('federal_agency') or {}
            naics = opp.get('naics_category') or {}
            psc = opp.get('psc_category') or {}
            pop = opp.get('place_of_performance') or {}
            desc_text = extract_description_text(opp.get('descriptions', []))
            all_opps.append({
                'id': oid,
                'name': opp.get('name', ''),
                'type': opp.get('opportunity_type', ''),
                'set_aside': opp.get('set_aside_type', ''),
                'due': (opp.get('due_date', '') or '')[:10],
                'posted': (opp.get('posted_date', '') or '')[:10],
                'agency': agency.get('name', '') if isinstance(agency, dict) else '',
                'agency_url': agency.get('govtribe_url', '') if isinstance(agency, dict) else '',
                'naics': (naics.get('govtribe_id', '') if isinstance(naics, dict) else '').replace('-N', ''),
                'naics_name': naics.get('name', '') if isinstance(naics, dict) else '',
                'psc': (psc.get('govtribe_id', '') if isinstance(psc, dict) else '').replace('-P', ''),
                'location': pop.get('name', '') if isinstance(pop, dict) else '',
                'url': opp.get('govtribe_url', ''),
                'desc_text': desc_text,
            })
    except Exception as e:
        print(f'Error parsing {f.name}: {e}')

# Add CBP inline results (no descriptions available from inline)
CBP_INLINE = [
    {'id': '40bcae4e8dea43b89db787b25fe53e17', 'name': 'USCG REGIONAL MULTIPLE AWARD CONSTRUCTION CONTRACT (RMACC III)', 'type': 'Pre-Solicitation', 'set_aside': 'No Set-Aside Used', 'due': '2026-12-31', 'posted': '2024-10-10', 'agency': 'Department of Homeland Security US Coast Guard', 'naics': '236220', 'naics_name': 'Commercial and Institutional Building Construction', 'psc': 'Z2AZ', 'location': 'Juneau, AK, USA', 'url': 'https://govtribe.com/opportunity/federal-contract-opportunity/uscg-regional-multiple-award-construction-contract-rmacc-iii-70z08725rrmacc003', 'desc_text': 'Design-Build Design-Bid-Build IDIQ Regional Multiple Award Construction Contracts RMACC perform General Construction Services bonding capacity mandatory attendance required Regional RMACC'},
    {'id': 'f635d62abd744a17b4348731dcb242d9', 'name': 'Design Bid Build (DBB) Construction Requirement for Alburg Springs Land Port of Entry (LPOE), Alburg, Vermont', 'type': 'Solicitation', 'set_aside': 'No Set-Aside Used', 'due': '2026-06-15', 'posted': '2026-04-15', 'agency': 'GSA Public Buildings Service', 'naics': '236220', 'naics_name': 'Commercial and Institutional Building Construction', 'psc': 'Y1AZ', 'location': 'Alburg, VT 05440, USA', 'url': 'https://govtribe.com/opportunity/federal-contract-opportunity/design-bid-build-dbb-construction-requirement-for-alburg-springs-land-port-of-entry-lpoe-alburg-vermont-47pb5126r0008-1', 'desc_text': 'Pre-Proposal Conference Site Visit Date May 6 2026 Time 12:00PM EST Location 303 Alburgh Springs Road Alburgh VT. Please register by May 1 2026 by 2:00PM EST.'},
    {'id': 'bf708b8816bf4fa087aa49e5e843b7c7', 'name': 'Construction of UEPH Barracks at Fort Campbell KY', 'type': 'Solicitation', 'set_aside': 'No Set-Aside Used', 'due': '2026-05-12', 'posted': '2026-03-13', 'agency': 'Department of the Army Corps of Engineers Engineering District Louisville', 'naics': '236220', 'naics_name': 'Commercial and Institutional Building Construction', 'psc': 'Y1FC', 'location': 'Fort Campbell, KY 42223, USA', 'url': 'https://govtribe.com/opportunity/federal-contract-opportunity/construction-of-ueph-barracks-at-fort-campbell-ky-w912qr26ra016', 'desc_text': 'Firm-Fixed-Price construction contract 236220 Commercial and Institutional Building Construction full and open competition Best Value Tradeoff'},
]
for o in CBP_INLINE:
    if o['id'] not in seen:
        all_opps.append(o)
        seen.add(o['id'])

print(f'Total opportunities loaded: {len(all_opps)}')

# ── Score all opportunities ───────────────────────────────────────────────────

FORECAST_RAW = [
    {'id': 'SAQMMA11D0079|SAQMMA12F2624', 'name': 'Delivery Order SAQMMA11D0079-SAQMMA12F2624', 'agency': 'Department of State Bureau of Administration', 'incumbent': 'Emcor Government Services, Inc.', 'value': 528674.89, 'end_date': '2026-08-03', 'naics': 'Facilities Support Services', 'set_aside': 'No Set-Aside Used', 'url': 'https://govtribe.com/award/federal-contract-award/delivery-order-saqmma11d0079-saqmma12f2624'},
    {'id': '127EAS21C0006', 'name': 'Definitive Contract 127EAS21C0006', 'agency': 'Department of Agriculture Forest Service R5', 'incumbent': 'Reliance Contractors Inc.', 'value': 153351, 'end_date': '2026-08-11', 'naics': 'Janitorial Services', 'set_aside': 'Total Small Business', 'url': 'https://govtribe.com/award/federal-contract-award/definitive-contract-127eas21c0006'},
    {'id': 'N6274217C1190', 'name': 'Definitive Contract N6274217C1190', 'agency': 'Department of the Navy Naval Facilities Engineering Command', 'incumbent': 'Fluor Federal Solutions, LLC', 'value': 399903026, 'end_date': '2026-08-22', 'naics': 'Facilities Support Services', 'set_aside': 'No Set-Aside Used', 'url': 'https://govtribe.com/award/federal-contract-award/definitive-contract-n6274217c1190'},
    {'id': 'HHSI102201400002C', 'name': 'Definitive Contract HHSI102201400002C', 'agency': 'Department of Health and Human Services Indian Health Service', 'incumbent': 'Sacred Power Corp.', 'value': 125250, 'end_date': '2027-04-30', 'naics': 'Commercial and Institutional Building Construction', 'set_aside': 'Total Small Business', 'url': 'https://govtribe.com/award/federal-contract-award/definitive-contract-hhsi102201400002c'},
]

targets = []
no_go_list = []
site_visit_kills = 0

for o in all_opps:
    score, kill, bonding, sv_warning, sv_date, qa_date = score_opp(o)
    agency = o.get('agency', '')
    priority = any(x in agency.lower() for x in ['gsa', 'public buildings', 'state', 'customs', 'border'])

    if score is None:
        if 'site visit' in (kill or '').lower() or 'conference' in (kill or '').lower():
            site_visit_kills += 1
        no_go_list.append({
            'id': o['id'], 'name': o['name'], 'verdict': 'NO-GO', 'score': 0,
            'kill_reason': kill, 'reason_summary': kill, 'recommended_action': '',
            'bonding_required': False, 'teaming_flag': False, 'priority_agency': priority,
            'score_breakdown': {}, 'agency': agency, 'agency_url': o.get('agency_url', ''),
            'opportunity_type': o.get('type', ''), 'set_aside_type': o.get('set_aside', ''),
            'posted_date': o.get('posted', ''), 'due_date': o.get('due', ''),
            'site_visit_date': sv_date, 'qa_rfi_date': '',
            'govtribe_url': o.get('url', ''), 'naics': o.get('naics_name', ''), 'psc': o.get('psc', ''),
        })
        continue

    verdict = 'GO' if score >= 60 else ('WATCH_TEAMING' if bonding and score >= 35 else 'WATCH' if score >= 35 else 'NO-GO')

    reason = f'Score {score}/100. {agency[:45]}. Set-aside: {o.get("set_aside", "")}.'
    if bonding:
        reason += ' Construction scope — bonding required.'
    if sv_warning:
        reason += f' NOTE: {sv_warning}.'

    action_map = {
        'GO': 'Draft capability statement and submit before deadline.',
        'WATCH': 'Review scope before committing.',
        'WATCH_TEAMING': 'Identify SB teaming partner with bonding capacity before responding.',
        'NO-GO': '',
    }
    action = action_map[verdict]
    if sv_warning and verdict in ('GO', 'WATCH', 'WATCH_TEAMING'):
        action = sv_warning + ' | ' + action

    record = {
        'id': o['id'], 'name': o['name'], 'verdict': verdict, 'score': score,
        'kill_reason': None, 'reason_summary': reason,
        'recommended_action': action,
        'bonding_required': bonding,
        'teaming_flag': (bonding and verdict == 'WATCH_TEAMING'),
        'site_visit_warning': sv_warning or '',
        'site_visit_date': sv_date,
        'qa_rfi_date': qa_date,
        'priority_agency': priority, 'score_breakdown': score,
        'agency': agency, 'agency_url': o.get('agency_url', ''),
        'opportunity_type': o.get('type', ''), 'set_aside_type': o.get('set_aside', ''),
        'posted_date': o.get('posted', ''), 'due_date': o.get('due', ''),
        'govtribe_url': o.get('url', ''), 'naics': o.get('naics_name', ''), 'psc': o.get('psc', ''),
    }

    if verdict == 'NO-GO':
        no_go_list.append(record)
    else:
        targets.append(record)

targets.sort(key=lambda x: -x['score'])

# Forecast
forecast = [
    {
        'id': f['id'], 'name': f['name'], 'agency': f['agency'],
        'incumbent': f['incumbent'], 'current_value': f['value'],
        'end_date': f['end_date'], 'action_date': action_date(f['end_date']),
        'forecast_stage': forecast_stage(f['end_date']),
        'naics': f['naics'], 'set_aside': f['set_aside'], 'govtribe_url': f['url'],
    }
    for f in FORECAST_RAW
]

go_count = len([t for t in targets if t['verdict'] == 'GO'])
watch_count = len([t for t in targets if t['verdict'] == 'WATCH'])
team_count = len([t for t in targets if t['verdict'] == 'WATCH_TEAMING'])

pipeline = {
    'generated_at': TODAY.isoformat(),
    'dashboard_url': 'https://jrmann22.github.io/tbg-pipeline/',
    'scan_summary': {
        'total_scanned': len(all_opps),
        'go': go_count,
        'watch': watch_count,
        'watch_teaming': team_count,
        'no_go': len(no_go_list),
        'forecast': len(forecast),
        'site_visit_kills': site_visit_kills,
    },
    'targets': targets,
    'no_go': no_go_list,
    'forecast': forecast,
}

Path(r'd:\QuantDesk\GovTribe\pipeline.json').write_text(json.dumps(pipeline, indent=2))

print(f'  GO:            {go_count}')
print(f'  WATCH:         {watch_count}')
print(f'  WATCH-TEAMING: {team_count}')
print(f'  NO-GO:         {len(no_go_list)}')
print(f'  Site visit kills: {site_visit_kills}')
print(f'  FORECAST:      {len(forecast)}')
print()
print('Top targets:')
for t in targets[:10]:
    sv = ' [SITE VISIT]' if t.get('site_visit_warning') else ''
    tm = ' [TEAMING]' if t['teaming_flag'] else ''
    print(f'  [{t["verdict"]:14s}] {t["score"]:3d}  due={t["due_date"]}  {t["name"][:55]}{tm}{sv}')
print()
print('Site-visit kills in NO-GO:')
for n in no_go_list:
    if 'site visit' in (n.get('kill_reason') or '').lower() or 'conference' in (n.get('kill_reason') or '').lower():
        print(f'  {n["name"][:65]}')
        print(f'    -> {n["kill_reason"]}')
