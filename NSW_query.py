# nsw_query.py (snippet)
import requests
from utils import arcgis_to_geojson, sanitize_nsw_props

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

def query(raw_input: str, max_records: int = 2000):
    where = "lotidstring IN ('13//DP1246224')"  # build your IN(...) as you already do

    params = {
        "f": "json",  # <-- ArcGIS JSON (more reliable than f=geojson on SIX)
        "where": where,
        "outFields": "lotidstring,lotnumber,sectionnumber,planlabel",
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,
        "resultRecordCount": max_records
    }
    r = requests.get(NSW_LAYER_URL, params=params, timeout=30)
    r.raise_for_status()
    arc = r.json()

    geo = arcgis_to_geojson(arc)  # <-- convert to GeoJSON FeatureCollection
    geo = sanitize_nsw_props(geo)
    return geo