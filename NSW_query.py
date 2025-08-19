# nsw_query.py
import re, requests
from typing import Dict, List, Tuple

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _parse_lotids(raw: str) -> List[str]:
    """Parse raw input into cleaned lotidstrings like 1//DP1139095"""
    if not raw: 
        return []
    tokens = re.split(r"[\n,;]+", raw)
    vals = []
    for t in tokens:
        s = t.strip()
        if not s: 
            continue
        # remove whitespace and symbols, uppercase
        s = re.sub(r"\s+", "", s).upper()
        s = re.sub(r"[^A-Z0-9/]", "", s)  # keep slash for lot/section/plan
        vals.append(s)
    # dedup preserving order
    out, seen = [], set()
    for v in vals:
        if v not in seen:
            out.append(v); seen.add(v)
    return out

def _build_where(lotids: List[str]) -> List[str]:
    clauses = []
    for group in _chunk(lotids, 100):
        quoted = ",".join([f"'{lp}'" for lp in group])
        # If NSW lotidstrings are stored uppercase (they usually are),
        # no need to wrap the field in UPPER()
        clauses.append(f"lotidstring IN ({quoted})")
    return clauses or ["1=2"]

def query(raw_input: str, max_records: int = 2000) -> Tuple[Dict, List[str]]:
    lotids = _parse_lotids(raw_input)
    clauses = _build_where(lotids)
    debug_urls = []
    features = []

    for where in clauses:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "lotidstring,lotnumber,sectionnumber,planlabel",
            "returnGeometry": "true",
            "geometryPrecision": 6,
            "outSR": 4326,
            "resultRecordCount": max_records
        }
        r = requests.get(NSW_LAYER_URL, params=params, timeout=30)
        debug_urls.append(r.url)
        r.raise_for_status()
        gj = r.json()
        features.extend(gj.get("features", []))

    return {"type": "FeatureCollection", "features": features}, debug_urls