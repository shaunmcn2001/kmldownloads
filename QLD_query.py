
import re, requests
from typing import List, Dict

QLD_LAYER_URL = "https://spatial-gis.information.qld.gov.au/arcgis/rest/services/PlanningCadastre/LandParcelPropertyFramework/MapServer/4/query"

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _parse_lotidstrings(raw: str) -> list:
    """
    Accept raw multiline / comma / semicolon separated text.
    Treat every non-empty token as a lotidstring for QLD, e.g. 1RP912949, 13SP12345.
    Strips spaces and uppercases.
    """
    if not raw:
        return []
    tokens = re.split(r"[\n,;]+", raw)
    vals = []
    for t in tokens:
        s = t.strip()
        if not s:
            continue
        s = re.sub(r"\s+", "", s).upper()
        # keep only A-Z0-9 for safety
        s = re.sub(r"[^A-Z0-9]", "", s)
        vals.append(s)
    # de-dup preserving order
    seen = set()
    out = []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out

def build_where_from_lotidstrings(lotids: list) -> list:
    """
    Build WHERE clauses using ONLY the lotidstring field.
    """
    clauses = []
    for group in _chunk(lotids, 100):
        in_vals = ",".join([f"UPPER('{lp}')" for lp in group])
        clauses.append(f"UPPER(lotidstring) IN ({in_vals})")
    return clauses or ["1=2"]

def query(raw_input: str, max_records: int = 4000) -> Dict:
    lotids = _parse_lotidstrings(raw_input)
    clauses = build_where_from_lotidstrings(lotids)

    all_features = []
    for where in clauses:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "resultRecordCount": max_records
        }
        r = requests.get(QLD_LAYER_URL, params=params, timeout=30)
        r.raise_for_status()
        gj = r.json()
        all_features.extend(gj.get("features", []))

    return {"type":"FeatureCollection","features":all_features}
