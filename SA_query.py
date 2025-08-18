
import requests, re
from typing import List, Dict
from utils import parse_bulk_entries, normalize_plan

# Public ePlanning parcels layer
SA_LAYER_URL = "https://lsa2.geohub.sa.gov.au/server/rest/services/ePlanning/DAP_Parcels/MapServer/1/query"

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def build_where(entries: List[Dict]) -> List[str]:
    plan_parcel_terms = []
    volfolio_terms = []

    for e in entries:
        k = e.get("kind")
        if k in ("lot_plan","lot_section_plan"):
            plan = normalize_plan(e["plan"])
            lot  = e["lot"]
            plan_parcel_terms.append(f"(UPPER(plan)=UPPER('{plan}') AND UPPER(parcel)=UPPER('{lot}'))")
        elif k == "volume_folio":
            volfolio_terms.append(f"(volume='{e['volume']}' AND folio='{e['folio']}')")

    clauses = []
    if plan_parcel_terms:
        for group in _chunk(plan_parcel_terms, 80):
            clauses.append(" OR ".join(group))
    if volfolio_terms:
        for group in _chunk(volfolio_terms, 100):
            clauses.append(" OR ".join(group))

    return clauses or ["1=2"]

def query(raw_input: str, max_records: int = 2000) -> Dict:
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
        r = requests.get(SA_LAYER_URL, params=params, timeout=30)
        r.raise_for_status()
        gj = r.json()
        all_features.extend(gj.get("features", []))

    return {"type":"FeatureCollection","features":all_features}
