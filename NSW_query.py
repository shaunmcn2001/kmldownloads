# NSW_query.py — NSW search by lotidstring only
import re
import requests
from typing import Dict, List, Tuple, Any
from utils import arcgis_to_geojson, sanitize_nsw_props

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"
IN_CHUNK = 100  # lotidstrings per group

def _chunk(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _preclean(raw: str) -> List[str]:
    """Extract lotidstrings like 13//DP1246224, 2/3/DP754253, A//DP397521."""
    if not raw:
        return []
    s = raw.strip()
    # unify separators
    s = re.sub(r"\s+(and|&)\s+", ";", s, flags=re.IGNORECASE)
    s = re.sub(r"[\n,;]+", ";", s)
    toks = [t.strip() for t in s.split(";") if t.strip()]
    out, seen = [], set()
    for t in toks:
        t = t.upper()
        t = re.sub(r"\s+", "", t)      # strip whitespace
        t = re.sub(r"[^A-Z0-9/]", "", t)  # keep only A–Z, 0–9, /
        if not t:
            continue
        # normalize to lotidstring: LOT//PLAN or LOT/SEC/PLAN
        parts = t.split("/")
        if len(parts) == 2:   # LOT/PLAN → LOT//PLAN
            t = f"{parts[0]}//{parts[1]}"
        elif len(parts) == 3:
            if parts[1] == "":
                t = f"{parts[0]}//{parts[2]}"
            else:
                t = f"{parts[0]}/{parts[1]}/{parts[2]}"
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out

def _build_where(lotids: List[str]) -> List[str]:
    """Build WHERE clauses using lotidstring only."""
    clauses = []
    for group in _chunk(lotids, IN_CHUNK):
        quoted = ",".join(f"'{lp}'" for lp in group)
        clauses.append(f"lotidstring IN ({quoted})")
    return clauses or ["1=2"]

def query(raw_input: str, max_records: int = 2000) -> Tuple[Dict[str, Any], List[str]]:
    debug: List[str] = []
    lotids = _preclean(raw_input)
    if not lotids:
        debug.append("NSW: no valid lotidstring parsed from input.")
        return {"type": "FeatureCollection", "features": []}, debug

    wheres = _build_where(lotids)
    all_arc_features: List[Dict[str, Any]] = []

    for w in wheres:
        params = {
            "f": "json",               # safer than f=geojson
            "where": w,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,
            "geometryPrecision": 6,
            "resultRecordCount": max_records
        }
        r = requests.get(NSW_LAYER_URL, params=params, timeout=60)
        debug.append(r.url)
        r.raise_for_status()
        arc = r.json()
        all_arc_features.extend(arc.get("features", []))

    geo = arcgis_to_geojson({"features": all_arc_features})
    geo = sanitize_nsw_props(geo)
    return geo, debug