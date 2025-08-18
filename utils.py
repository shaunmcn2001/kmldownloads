
import re
from typing import List, Tuple, Dict

# ---------- Parsing helpers ----------
def normalize_plan(plan: str) -> str:
    if not plan: return ""
    p = plan.upper().replace(" ", "")
    # NSW plans often DP/SP/CP; SA plans like D12345 etc; QLD plans like RP, SP, CP.
    # Keep alphanum only
    p = re.sub(r"[^A-Z0-9]", "", p)
    return p

def normalize_lot(lot: str) -> str:
    if not lot: return ""
    l = lot.upper().strip()
    l = re.sub(r"[^A-Z0-9-]", "", l)  # allow A/B lots and ranges like 1-3
    return l

def expand_lot_ranges(lot_str: str) -> List[str]:
    # Accept "1-3,5,7A" -> ["1","2","3","5","7A"]
    lots = []
    for token in re.split(r"[,\s]+", lot_str.strip()):
        if not token: 
            continue
        m = re.fullmatch(r"(\d+)\-(\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if a <= b else -1
            for n in range(a, b + step, step):
                lots.append(str(n))
        else:
            lots.append(token)
    return lots

def parse_bulk_entries(raw: str) -> List[Dict]:
    """
    Accepts free text and extracts entries in these forms:
      - LOT//PLAN                (e.g., 13//DP1242624)
      - LOT/SECTION//PLAN        (e.g., 13/1//DP1242624)
      - LOTPLAN (QLD)            (e.g., 1RP912949, 13SP12345)
      - lotidstring (NSW) tokens (e.g., LOT 13 DP1242624)
      - SA volume/folio          (e.g., 5100/123)
    Returns list of dicts with keys: kind, lot, section, plan, lotplan, lotidstring, volume, folio
    """
    entries = []
    if not raw:
        return entries

    # Split by newline or comma/semicolon
    pieces = re.split(r"[\n;,]+", raw)
    for piece in pieces:
        s = piece.strip()
        if not s:
            continue

        # NSW/SA style with section: LOT/SECTION//PLAN
        m = re.fullmatch(r"(?i)\s*([A-Z0-9\-]+)\s*/\s*([A-Z0-9\-]+)\s*//\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lot = normalize_lot(m.group(1))
            section = normalize_lot(m.group(2))
            plan = normalize_plan(m.group(3))
            entries.append({"kind":"lot_section_plan","lot":lot,"section":section,"plan":plan})
            continue

        # LOT//PLAN
        m = re.fullmatch(r"(?i)\s*([A-Z0-9,\-\s]+)\s*//\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lots = expand_lot_ranges(normalize_lot(m.group(1)))
            plan = normalize_plan(m.group(2))
            for lot in lots:
                entries.append({"kind":"lot_plan","lot":lot,"section":None,"plan":plan})
            continue

        # SA Volume/Folio
        m = re.fullmatch(r"\s*(\d{1,5})\s*/\s*(\d{1,6})\s*", s)
        if m:
            entries.append({"kind":"volume_folio","volume":m.group(1), "folio":m.group(2)})
            continue

        # QLD LotPlan like 1RP912949 or 13SP12345
        m = re.fullmatch(r"(?i)\s*(\d+[a-z]{1,3}\d+)\s*", s)
        if m:
            entries.append({"kind":"lotplan","lotplan":m.group(1).upper()})
            continue

        # NSW lotidstring e.g., LOT 13 DP1242624
        if s.upper().startswith("LOT ") and " DP" in s.upper():
            entries.append({"kind":"lotidstring","lotidstring":re.sub(r"\s+"," ",s.upper().strip())})
            continue

        # Fallback: try to detect "LOT, PLAN"
        m = re.fullmatch(r"(?i)\s*([A-Z0-9\-]+)\s*,\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lot = normalize_lot(m.group(1))
            plan = normalize_plan(m.group(2))
            entries.append({"kind":"lot_plan","lot":lot,"section":None,"plan":plan})
            continue

        # If nothing matched, keep the raw token so the caller can log/skip
        entries.append({"kind":"unknown","raw":s})

    return entries
