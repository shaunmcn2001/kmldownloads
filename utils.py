import re
from typing import List, Tuple, Dict, Any

# ---------- Parsing helpers ----------
def normalize_plan(plan: str) -> str:
    if not plan: return ""
    p = plan.upper().replace(" ", "")
    p = re.sub(r"[^A-Z0-9]", "", p)
    return p

def normalize_lot(lot: str) -> str:
    if not lot: return ""
    l = lot.upper().strip()
    l = re.sub(r"[^A-Z0-9-]", "", l)  # allow A/B lots and ranges like 1-3
    return l

def expand_lot_ranges(lot_str: str) -> List[str]:
    # Accept "1-3,5,7A" -> ["1","2","3","5","7A"]
    lots = []
    for token in re.split(r"[,\s]+", lot_str.strip()):
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\-(\d+)", token)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if a <= b else -1
            for n in range(a, b + step, step):
                lots.append(str(n))
        else:
            lots.append(token)
    return lots

def parse_bulk_entries(raw: str) -> List[Dict[str, Any]]:
    """
    Accepts free text and extracts entries in these forms:
      - LOT//PLAN                (e.g., 13//DP1242624)
      - LOT/SECTION//PLAN        (e.g., 13/1//DP1242624)
      - LOTPLAN (QLD)            (e.g., 1RP912949, 13SP12345)
      - lotidstring (NSW) tokens (e.g., LOT 13 DP1242624)
      - SA volume/folio          (e.g., 5100/123)
    Returns list of dicts with keys: kind, lot, section, plan, lotplan, lotidstring, volume, folio
    """
    entries: List[Dict[str, Any]] = []
    if not raw:
        return entries

    pieces = re.split(r"[\n;,]+", raw)
    for piece in pieces:
        s = piece.strip()
        if not s:
            continue

        # LOT/SECTION//PLAN
        m = re.fullmatch(r"(?i)\s*([A-Z0-9\-]+)\s*/\s*([A-Z0-9\-]+)\s*//\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lot = normalize_lot(m.group(1))
            section = normalize_lot(m.group(2))
            plan = normalize_plan(m.group(3))
            entries.append({"kind": "lot_section_plan", "lot": lot, "section": section, "plan": plan})
            continue

        # LOT//PLAN  (allow ranges in the lot part: e.g. "1-3//DP1234")
        m = re.fullmatch(r"(?i)\s*([A-Z0-9,\-\s]+)\s*//\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lots = expand_lot_ranges(normalize_lot(m.group(1)))
            plan = normalize_plan(m.group(2))
            for lot in lots:
                entries.append({"kind": "lot_plan", "lot": lot, "section": None, "plan": plan})
            continue

        # SA Volume/Folio
        m = re.fullmatch(r"\s*(\d{1,5})\s*/\s*(\d{1,6})\s*", s)
        if m:
            entries.append({"kind": "volume_folio", "volume": m.group(1), "folio": m.group(2)})
            continue

        # QLD LotPlan like 1RP912949 or 13SP12345
        m = re.fullmatch(r"(?i)\s*(\d+[a-z]{1,3}\d+)\s*", s)
        if m:
            entries.append({"kind": "lotplan", "lotplan": m.group(1).upper()})
            continue

        # NSW lotidstring e.g., LOT 13 DP1242624
        if s.upper().startswith("LOT ") and " DP" in s.upper():
            entries.append({"kind": "lotidstring", "lotidstring": re.sub(r"\s+", " ", s.upper().strip())})
            continue

        # Fallback: "LOT, PLAN"
        m = re.fullmatch(r"(?i)\s*([A-Z0-9\-]+)\s*,\s*([A-Z]+\s*\d+)\s*", s)
        if m:
            lot = normalize_lot(m.group(1))
            plan = normalize_plan(m.group(2))
            entries.append({"kind": "lot_plan", "lot": lot, "section": None, "plan": plan})
            continue

        entries.append({"kind": "unknown", "raw": s})

    return entries


# ---------- ArcGIS JSON → GeoJSON converter ----------
def _arcgis_geom_to_geojson(geom: Dict[str, Any]) -> Dict[str, Any] | None:
    if not geom:
        return None

    # Point
    if "x" in geom and "y" in geom:
        return {"type": "Point", "coordinates": [geom["x"], geom["y"]]}

    # MultiPoint
    if "points" in geom and isinstance(geom["points"], list):
        pts = geom["points"]
        if not pts:
            return None
        if len(pts) == 1:
            return {"type": "Point", "coordinates": pts[0]}
        return {"type": "MultiPoint", "coordinates": pts}

    # Polyline
    if "paths" in geom and isinstance(geom["paths"], list):
        paths = [p for p in geom["paths"] if p]
        if not paths:
            return None
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}

    # Polygon
    if "rings" in geom and isinstance(geom["rings"], list):
        rings = [r for r in geom["rings"] if r]
        if not rings:
            return None
        if len(rings) == 1:
            return {"type": "Polygon", "coordinates": [rings[0]]}
        # Minimal multi-ring mapping (no hole orientation handling)
        return {"type": "MultiPolygon", "coordinates": [[[ring]] for ring in rings]}

    return None

def arcgis_to_geojson(fc_arcgis: Dict[str, Any]) -> Dict[str, Any]:
    feats = fc_arcgis.get("features") or []
    out_features: List[Dict[str, Any]] = []
    for f in feats:
        props = f.get("attributes") or {}
        geom = _arcgis_geom_to_geojson(f.get("geometry") or {})
        if geom is None:
            continue
        out_features.append({
            "type": "Feature",
            "properties": props,
            "geometry": geom
        })
    return {"type": "FeatureCollection", "features": out_features}


# ---------- NSW properties sanitizer ----------
def sanitize_nsw_props(geojson_fc: dict) -> dict:
    for f in geojson_fc.get("features", []):
        p = f.setdefault("properties", {})

        # Force strings (avoid 13.0 etc.)
        for k in ("lotnumber", "sectionnumber", "planlabel", "lotidstring"):
            if k in p and p[k] is not None:
                p[k] = str(p[k]).strip()

        # Normalize plan casing
        if p.get("planlabel"):
            p["planlabel"] = p["planlabel"].replace(" ", "").upper()

        # Canonical lotidstring → label
        canon = p.get("lotidstring")
        if not canon:
            lot = p.get("lotnumber", "")
            sec = p.get("sectionnumber", "")
            plan = p.get("planlabel", "")
            if lot and plan:
                canon = f"{lot}/{sec}/{plan}" if sec else f"{lot}//{plan}"
        else:
            up = canon.upper()
            if up.startswith("LOT ") and " DP" in up:
                parts = up.split()
                if len(parts) >= 3:
                    lot = parts[1]
                    plan = parts[-1].replace(" ", "")
                    canon = f"{lot}//{plan}"

        if canon:
            p["lotidstring"] = canon
            p["label"] = canon

        # Drop noisy props
        for noisy in ("OBJECTID", "Shape_Area", "Shape_Length"):
            p.pop(noisy, None)

    return geojson_fc