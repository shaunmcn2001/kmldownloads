# download.py
import os
from typing import Dict, List, Optional, Iterable, Tuple
import simplekml

DEFAULT_STYLE = {
    "line_width": 1.5,
    "line_color": "ffaaaaaa",   # aabbggrr (KML is ABGR)
    "poly_color": "7d00ff00",   # 0x7d (opacity) + 00ff00 (green) -> ABGR order
}

STATE_COLOURS = {
    "NSW": "7d0000ff",  # semi-blue
    "QLD": "7d00ff00",  # semi-green
    "SA":  "7d00ffff",  # semi-yellow (red+green)
}

def _kml_color_from_rgba(r: int, g: int, b: int, a: int) -> str:
    # KML color is aabbggrr hex
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

def _feature_popup_html(props: Dict) -> str:
    keys = [
        "source","state","lot","plan","lotplan","section","lotidstring","parcel",
        "volume","folio","locality","shire_name","planlabel","lotnumber",
        "sectionnumber","lot_area","shape_Area","st_area(shape)"
    ]
    rows = []
    for k in keys:
        if k in props and props[k] not in (None, "", " "):
            rows.append(f"<tr><th style='text-align:left;padding-right:8px'>{k}</th><td>{props[k]}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"

# ---------- NEW: robust ring iterator ----------
def _iter_outer_rings(geom: Dict) -> Iterable[List[Tuple[float, float]]]:
    """
    Yield outer rings as [(lon, lat), ...] from a GeoJSON Polygon/MultiPolygon.
    Ignores holes. Safely flattens nesting and 3D coords.
    """
    if not geom:
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")

    def as_positions(seq):
        out: List[Tuple[float, float]] = []
        for pt in seq or []:
            # pt can be [x,y], [x,y,z], or nested
            if isinstance(pt, (list, tuple)) and pt and isinstance(pt[0], (int, float)):
                x = float(pt[0]); y = float(pt[1]) if len(pt) > 1 else 0.0
                out.append((x, y))
        return out

    if gtype == "Polygon":
        # coords: [ [ring0], [hole1], ... ]
        if isinstance(coords, list) and coords:
            ring0 = coords[0]
            ring = as_positions(ring0)
            if ring:
                yield ring
    elif gtype == "MultiPolygon":
        # coords: [ [ [ring0], [hole1], ... ], ... ]
        if isinstance(coords, list):
            for poly in coords:
                ring0 = poly[0] if (isinstance(poly, list) and poly) else []
                ring = as_positions(ring0)
                if ring:
                    yield ring
    # else: ignore non-polygons for KML polygon export

def save_kml(
    feature_collection: Dict,
    out_dir: str,
    filename: str = "parcels.kml",
    state: Optional[str] = None,
    colour: Optional[str] = None,
    line_width: float = 1.5
) -> str:
    """
    Save a GeoJSON FeatureCollection to a styled KML file with popups.
    Returns the file path.
    Handles Polygon and MultiPolygon (outer ring only); skips non-polygons.
    """
    os.makedirs(out_dir, exist_ok=True)
    kml = simplekml.Kml()

    poly_colour = colour or (STATE_COLOURS.get(state.upper(), DEFAULT_STYLE["poly_color"]) if state else DEFAULT_STYLE["poly_color"])
    line_colour = DEFAULT_STYLE["line_color"]

    polystyle = simplekml.PolyStyle(color=poly_colour, fill=1, outline=1)
    linestyle = simplekml.LineStyle(color=line_colour, width=line_width)

    for feat in feature_collection.get("features", []) or []:
        geom = feat.get("geometry", {}) or {}
        props = (feat.get("properties", {}) or {}).copy()

        if state:
            props["state"] = state

        # name for placemark
        name = (
            props.get("label") or props.get("lotidstring") or props.get("lotplan")
            or props.get("planparcel") or props.get("plan") or "parcel"
        )

        wrote_any = False
        for ring in _iter_outer_rings(geom):
            if not ring:
                continue
            # Ensure ring is closed
            if ring[0] != ring[-1]:
                ring = ring + [ring[0]]
            p = kml.newpolygon(name=name)
            p.outerboundaryis = ring              # simplekml accepts (lon, lat) pairs
            p.style.polystyle = polystyle
            p.style.linestyle = linestyle
            p.description = _feature_popup_html(props)
            wrote_any = True

        # If geometry wasn’t a polygon, skip silently
        if not wrote_any:
            continue

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
