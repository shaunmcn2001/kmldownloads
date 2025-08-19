# NSW_query.py — lotidstring-only, QLD-style with GeoJSON→ArcGIS fallback
import re, requests
from typing import Dict, List, Tuple, Any
from utils import arcgis_to_geojson, sanitize_nsw_props

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"
CHUNK = 80  # keep URLs short; NSW chokes on very long IN lists

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _parse_lotidstrings(raw: str) -> List[str]:
    """
    Accept only lotidstring formats:
      LOT//PLAN  (e.g. 13//DP1246224)
      LOT/SEC/PLAN (e.g. 2/3/DP754253)
      LOT/PLAN  -> normalized to LOT//PLAN
    """
    if not raw: return []
    s = re.sub(r"\s+(and|&)\s+", ";", raw, flags=re.IGNORECASE)
    s = re.sub(r"[\n,;]+", ";", s)
    toks = [t.strip() for t in s.split(";") if t.strip()]
    out, seen = [], set()
    for t in toks:
        t = re.sub(r"\s+", "", t.upper())
        t = re.sub(r"[^A-Z0-9/]", "", t)  # keep slashes
        if not t: continue
        parts = t.split("/")
        if len(parts) == 2:    # LOT/PLAN -> LOT//PLAN
            t = f"{parts[0]}//{parts[1]}"
        elif len(parts) == 3:  # LOT/SEC/PLAN or LOT//PLAN
            t = f"{parts[0]}/{parts[1]}/{parts[2]}" if parts[1] else f"{parts[0]}//{parts[2]}"
        # dedupe
        if t and t not in seen:
            out.append(t); seen.add(t)
    return out

def _build_where(lotids: List[str]) -> List[str]:
    clauses = []
    for group in _chunk(lotids, CHUNK):
        quoted = ",".join(f"'{lp}'" for lp in group)  # avoid UPPER() to use the index
        clauses.append(f"lotidstring IN ({quoted})")
    return clauses or ["1=2"]

def _fetch_geojson(where: str, max_records: int) -> Dict[str, Any]:
    """Fast path: ask server for GeoJSON; NSW sometimes fails this (we catch & fallback)."""
    params = {
        "f": "geojson",
        "where": where,
        "outFields": "*",              # subset fields can cause 0 results with geometry on NSW
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,
        "resultRecordCount": max_records
    }
    r = requests.get(NSW_LAYER_URL, params=params, timeout=45)
    r.raise_for_status()
    return r.json()

def _fetch_arcgis(where: str, max_records: int) -> Dict[str, Any]:
    """Fallback: stable ArcGIS JSON (convert locally)."""
    params = {
        "f": "json",
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,
        "resultRecordCount": max_records
    }
    r = requests.get(NSW_LAYER_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def query(raw_input: str, max_records: int = 2000) -> Tuple[Dict[str, Any], List[str]]:
    lotids = _parse_lotidstrings(raw_input)
    debug: List[str] = []
    if not lotids:
        debug.append("NSW: no valid lotidstring parsed from input.")
        return {"type": "FeatureCollection", "features": []}, debug

    wheres = _build_where(lotids)

    all_features: List[Dict[str, Any]] = []
    for w in wheres:
        # try f=geojson first
        try:
            gj = _fetch_geojson(w, max_records)
            debug.append(f"NSW geojson OK: {NSW_LAYER_URL}?where={w}")
            all_features.extend(gj.get("features", []))
            continue
        except Exception as e:
            debug.append(f"NSW geojson failed (fallback to json): {e}")

        # fallback to f=json + convert
        arc = _fetch_arcgis(w, max_records)
        debug.append(f"NSW json OK: {NSW_LAYER_URL}?where={w}")
        gj_chunk = arcgis_to_geojson(arc)
        all_features.extend(gj_chunk.get("features", []))

    fc = {"type": "FeatureCollection", "features": all_features}
    fc = sanitize_nsw_props(fc)  # adds clean 'label' and tidies props
    return fc, debug