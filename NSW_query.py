
import requests
from typing import List, Dict
from utils import parse_bulk_entries, normalize_plan

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def build_where(entries: List[Dict]) -> List[str]:
    """
    Return list of WHERE clauses (chunked) for NSW layer.
    We prefer planlabel + lotnumber/sectionnumber. Fallback to lotidstring.
    """
    plan_lot_terms = []
    lotid_terms = []

    for e in entries:
        k = e.get("kind")
        if k in ("lot_plan","lot_section_plan"):
            plan = e["plan"]
            lot = e["lot"]
            sec = e.get("section")
            base = f"UPPER(planlabel)=UPPER('{plan}') AND UPPER(lotnumber)=UPPER('{lot}')"
            if sec:
                base += f" AND UPPER(sectionnumber)=UPPER('{sec}')"
            plan_lot_terms.append(f"({base})")
        elif k == "lotidstring":
            lotid = e["lotidstring"].replace("'", "''")
            lotid_terms.append(f"UPPER(lotidstring)=UPPER('{lotid}')")
        else:
            # ignore other kinds here
            pass

    where_clauses = []
    if plan_lot_terms:
        # ArcGIS has URL length limits; chunk terms
        for group in _chunk(plan_lot_terms, 50):
            where_clauses.append(" OR ".join(group))
    if lotid_terms:
        for group in _chunk(lotid_terms, 100):
            where_clauses.append(" OR ".join(group))

    return where_clauses or ["1=2"]  # nothing matched

def query(raw_input: str, max_records: int = 2000) -> Dict:
    """
    Returns a GeoJSON FeatureCollection for matching NSW lots.
    """
    entries = parse_bulk_entries(raw_input)
    clauses = build_where(entries)

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

    return {"type":"FeatureCollection","features":all_features}
