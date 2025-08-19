# NSW_query.py
import re
import requests
from typing import List, Dict
from utils import parse_bulk_entries, normalize_plan

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

# Reasonable per-request IN-list size to avoid URL length issues
IN_CHUNK = 150

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _preclean_raw(raw: str) -> str:
    """
    Very light pre-clean so users can paste '1DP1139095 and 1DP1129814'
    or comma/newline-separated lists; we normalize to semicolons.
    """
    if not raw:
        return ""
    s = raw.strip()
    # Replace common joiners with semicolons
    s = re.sub(r"\s+(and|&)\s+", ";", s, flags=re.IGNORECASE)
    s = s.replace(",", ";")
    s = s.replace("\n", ";")
    s = s.replace("\r", ";")
    # Collapse duplicate separators
    s = re.sub(r";{2,}", ";", s)
    return s

def _build_where_lotid(entries: List[Dict]) -> List[str]:
    """
    Build NSW WHERE clauses using *only* lotidstring, grouped as IN(...) lists.
    Accepts kinds: 'lotidstring', 'lot_plan', 'lot_section_plan'.
    """
    lotids: List[str] = []

    for e in entries:
        k = e.get("kind")
        if k == "lotidstring":
            lotid = e["lotidstring"].upper().replace("'", "''")
            lotids.append(lotid)
        elif k in ("lot_plan", "lot_section_plan"):
            # Construct lotidstring from parts for consistency
            lot = str(e["lot"]).upper()
            plan = normalize_plan(e["plan"]).upper()
            sec = str(e["section"]).upper() if e.get("section") else ""
            lotid = f"{lot}/{sec}/{plan}" if sec else f"{lot}//{plan}"
            lotid = lotid.replace("'", "''")
            lotids.append(lotid)
        else:
            # ignore anything else
            pass

    # Deduplicate and sort for stable requests
    lotids = sorted(set(lotids))

    if not lotids:
        return ["1=2"]

    where_clauses = []
    for group in _chunk(lotids, IN_CHUNK):
        in_list = ", ".join(f"'{x}'" for x in group)
        # Case-insensitive compare by uppercasing the field.
        # If you know the service values are consistently cased, switch to:
        # where = f"lotidstring IN ({in_list})"
        where = f"UPPER(lotidstring) IN ({in_list})"
        where_clauses.append(where)

    return where_clauses

def query(raw_input: str, max_records: int = 2000) -> Dict:
    """
    Returns a GeoJSON FeatureCollection for matching NSW lots using lotidstring-only.
    Accepts loose input: '1DP1139095 and 1DP1129814', commas, semicolons, newlines, etc.
    """
    cleaned = _preclean_raw(raw_input)
    entries = parse_bulk_entries(cleaned)  # expands ranges, handles A/DP..., 2/3/DP..., etc.
    clauses = _build_where_lotid(entries)

    all_features = []
    for where in clauses:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "resultRecordCount": max_records
        }
        r = requests.get(NSW_LAYER_URL, params=params, timeout=30)
        r.raise_for_status()
        gj = r.json()
        feats = gj.get("features", [])
        all_features.extend(feats)

    return {"type": "FeatureCollection", "features": all_features}
