# nsw_query.py
import re
import time
import requests
from typing import List, Dict, Any, Iterable
from utils import parse_bulk_entries, normalize_plan

NSW_LAYER_URL = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9/query"

# Make each request lighter to avoid server stalls
IN_CHUNK = 50  # was 150

# Network tuning
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 120
MAX_RETRIES = 3
BACKOFF_BASE_S = 2  # 2, 4, 8 seconds...


def _chunk(seq: Iterable[Any], n: int):
    it = list(seq)
    for i in range(0, len(it), n):
        yield it[i:i + n]


def _preclean_raw(raw: str) -> str:
    """
    Normalize separators so users can paste:
    - '1DP1139095 and 1DP1129814'
    - comma/semicolon/newline-separated lists
    """
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"\s+(and|&)\s+", ";", s, flags=re.IGNORECASE)
    s = s.replace(",", ";").replace("\n", ";").replace("\r", ";")
    s = re.sub(r";{2,}", ";", s)
    return s


def _build_where_lotid(entries: List[Dict]) -> List[str]:
    """
    Build NSW WHERE clauses using only lotidstring in IN(...) lists.
    Accepts kinds: 'lotidstring', 'lot_plan', 'lot_section_plan'.
    """
    lotids: List[str] = []

    for e in entries:
        k = e.get("kind")
        if k == "lotidstring":
            lotid = e["lotidstring"].upper().replace("'", "''")
            lotids.append(lotid)
        elif k in ("lot_plan", "lot_section_plan"):
            lot = str(e["lot"]).upper()
            plan = normalize_plan(e["plan"]).upper()
            sec = str(e["section"]).upper() if e.get("section") else ""
            lotid = f"{lot}/{sec}/{plan}" if sec else f"{lot}//{plan}"
            lotids.append(lotid.replace("'", "''"))
        # ignore anything else

    lotids = sorted(set(lotids))
    if not lotids:
        return ["1=2"]

    where_clauses = []
    for group in _chunk(lotids, IN_CHUNK):
        in_list = ", ".join(f"'{x}'" for x in group)
        # If service casing is consistent, you can drop UPPER() for index use:
        # where = f"lotidstring IN ({in_list})"
        where = f"UPPER(lotidstring) IN ({in_list})"
        where_clauses.append(where)

    return where_clauses


def _safe_get(url: str, params: Dict[str, Any], retries: int = MAX_RETRIES):
    """
    GET with retries on read/connect timeouts and 5xx HTTP errors.
    Backoff: 2, 4, 8 seconds...
    """
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
            )
            # Retry on 5xx
            if 500 <= r.status_code < 600:
                last_err = requests.HTTPError(f"{r.status_code} Server Error")
                raise last_err
            r.raise_for_status()
            return r
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(BACKOFF_BASE_S ** (attempt + 1))
                continue
            raise
    # Shouldn’t reach here
    raise last_err if last_err else RuntimeError("Unknown request error")


def query(raw_input: str, max_records: int = 2000) -> Dict[str, Any]:
    """
    Returns a GeoJSON FeatureCollection for matching NSW lots using lotidstring-only.
    Accepts loose input: '1DP1139095 and 1DP1129814', commas, semicolons, newlines, ranges, etc.
    """
    cleaned = _preclean_raw(raw_input)
    entries = parse_bulk_entries(cleaned)
    clauses = _build_where_lotid(entries)

    all_features = []
    for where in clauses:
        params = {
            "f": "geojson",
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "resultRecordCount": max_records,
        }
        r = _safe_get(NSW_LAYER_URL, params=params, retries=MAX_RETRIES)
        try:
            gj = r.json()
        except ValueError:
            # If NSW responds with non-JSON intermittently, skip this chunk
            gj = {}
        feats = gj.get("features", []) if isinstance(gj, dict) else []
        all_features.extend(feats)

    return {"type": "FeatureCollection", "features": all_features}
