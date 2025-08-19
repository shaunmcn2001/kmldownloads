# NSW_query.py
import re
import requests
from typing import Dict, List, Tuple, Any
from utils import arcgis_to_geojson, sanitize_nsw_props  # ensure these exist in utils.py

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"
IN_CHUNK = 100  # lotidstrings per group

def _chunk(lst: List[str], n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def _preclean(raw: str) -> List[str]:
    """Split on newline/commas/semicolons/'and'; keep slashes for lot/section/plan."""
    if not raw:
        return []
    s = raw.strip()
    s = re.sub(r"\s+(and|&)\s+", ";", s, flags=re.IGNORECASE)
    s = re.sub(r"[\n,;]+", ";", s)
    toks = [t.strip() for t in s.split(";") if t.strip()]
    out, seen = [], set()
    for t in toks:
        t = re.sub(r"\s+", "", t).upper()
        t = re.sub(r"[^A-Z0-9/]", "", t)  # keep '/'
        if not t:
            continue
        # LOT//PLAN, LOT/SEC/PLAN, or compact LOT+PLAN (e.g., 13DP1246224)
        if "/" in t:
            parts = t.split("/")
            if len(parts) == 2:            # LOT/PLAN → LOT//PLAN
                t = f"{parts[0]}//{parts[1]}"
            elif len(parts) == 3:
                if parts[1] == "":         # LOT//PLAN (empty sec)
                    t = f"{parts[0]}//{parts[2]}"
                else:                      # LOT/SEC/PLAN
                    t = f"{parts[0]}/{parts[1]}/{parts[2]}"
        else:
            m = re.match(r"^([A-Z0-9]+)(DP|SP|CP|RP|BUP)(\d+)$", t)  # compact LOT+PLAN
            if m:
                t = f"{m.group(1)}//{m.group(2)}{m.group(3)}"
        if t and t not in seen:
            out.append(t); seen.add(t)
    return out

def _build_where(lotids: List[str]) -> List[str]:
    clauses = []
    for group in _chunk(lotids, IN_CHUNK):
        quoted = ",".join(f"'{lp}'" for lp in group)  # NSW stores uppercase; index-friendly
        clauses.append(f"lotidstring IN ({quoted})")
    return clauses or ["1=2"]

def _count(where: str) -> int:
    params = {"f": "json", "where": where, "returnCountOnly": "true"}
    r = requests.get(NSW_LAYER_URL, params=params, timeout=30)
    r.raise_for_status()
    return int(r.json().get("count", 0))

# ---------- IDs-first fetch (reliable on SIX) ----------
def _get_ids(where: str) -> List[int]:
    params = {"f": "json", "where": where, "returnIdsOnly": "true"}
    r = requests.get(NSW_LAYER_URL, params=params, timeout=30)
    r.raise_for_status()
    ids = r.json().get("objectIds") or []
    return [int(x) for x in ids] if ids else []

def _fetch_by_ids(ids: List[int], max_records: int) -> Dict[str, Any]:
    if not ids:
        return {"features": [], "objectIdFieldName": "OBJECTID"}
    params = {
        "f": "json",
        "objectIds": ",".join(map(str, ids[:max_records])),
        "outFields": "*",                 # keep * to avoid weird zero-feature bug
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,
    }
    r = requests.get(NSW_LAYER_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()
# -------------------------------------------------------

def query(raw_input: str, max_records: int = 2000) -> Tuple[Dict[str, Any], List[str]]:
    debug: List[str] = []
    lotids = _preclean(raw_input)
    if not lotids:
        debug.append("NSW: no parsable lotidstrings from input.")
        return {"type": "FeatureCollection", "features": []}, debug

    wheres = _build_where(lotids)
    all_arc: Dict[str, Any] = {"features": []}
    seen_ids: set[int] = set()

    for w in wheres:
        try:
            cnt = _count(w)
            debug.append(f"NSW WHERE: {w} -> count={cnt}")
            if cnt == 0:
                continue

            ids = _get_ids(w)
            debug.append(f"NSW IDs: {ids}")
            if not ids:
                debug.append("NSW: _get_ids returned no IDs despite count>0.")
                continue

            arc = _fetch_by_ids(ids, max_records=max_records)
            feats = arc.get("features", []) or []
            oid_field = arc.get("objectIdFieldName", "OBJECTID")
            have_geom = sum(1 for f in feats if f.get("geometry"))
            debug.append(f"NSW FETCH by IDs: returned={len(feats)} features, {have_geom} with geometry")

            # De-dup across chunks by OBJECTID
            for f in feats:
                oid = f.get("attributes", {}).get(oid_field)
                if oid is None or oid in seen_ids:
                    continue
                seen_ids.add(oid)
                all_arc["features"].append(f)

        except Exception as e:
            debug.append(f"NSW error on WHERE chunk: {e}")

    # Enforce max_records globally (after de-dup)
    if max_records and len(all_arc["features"]) > max_records:
        all_arc["features"] = all_arc["features"][:max_records]
        debug.append(f"NSW capped to max_records={max_records}")

    # Convert ArcGIS JSON → GeoJSON and sanitize props
    geo = arcgis_to_geojson(all_arc)
    geo = sanitize_nsw_props(geo)
    return geo, debug