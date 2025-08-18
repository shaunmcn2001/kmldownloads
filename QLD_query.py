import re, requests
from typing import Dict, List, Tuple

QLD_LAYER_URL = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _parse_lotplans(raw: str) -> List[str]:
    if not raw: return []
    tokens = re.split(r"[\n,;]+", raw)
    vals = []
    for t in tokens:
        s = t.strip()
        if not s: continue
        s = re.sub(r"\s+", "", s).upper()
        s = re.sub(r"[^A-Z0-9]", "", s)
        vals.append(s)
    # dedup
    out, seen = [], set()
    for v in vals:
        if v not in seen:
            out.append(v); seen.add(v)
    return out

def _build_where(lotplans: List[str]) -> List[str]:
    clauses = []
    for group in _chunk(lotplans, 100):
        quoted = ",".join([f"UPPER('{lp}')" for lp in group])
        clauses.append(f"UPPER(lotplan) IN ({quoted})")
    return clauses or ["1=2"]

def query(raw_input: str, max_records: int = 4000) -> Tuple[Dict, List[str]]:
    lotplans = _parse_lotplans(raw_input)
    clauses = _build_where(lotplans)
    debug_urls = []
    features = []

    for where in clauses:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "resultRecordCount": max_records
        }
        r = requests.get(QLD_LAYER_URL, params=params, timeout=30)
        debug_urls.append(r.url)
        r.raise_for_status()
        gj = r.json()
        features.extend(gj.get("features", []))

    return {"type":"FeatureCollection", "features": features}, debug_urls
