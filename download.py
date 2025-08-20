# download.py
import os
from typing import Dict, List, Optional, Iterable, Tuple
import simplekml

DEFAULT_STYLE = {
    "line_width": 1.5,
    "line_color": "ffaaaaaa",   # aabbggrr (KML is ABGR)
    "poly_color": "7d00ff00",   # opacity 0x7d + 00ff00 -> ABGR
}

STATE_COLOURS = {
    "NSW": "7d0000ff",
    "QLD": "7d00ff00",
    "SA":  "7d00ffff",
}

def _feature_popup_html(props: Dict) -> str:
    preferred = [
        "controllingauthorityoid","planoid","plannumber","planlabel","itstitlestatus",
        "itslotid","stratumlevel","hasstratum","classsubtype","lotnumber","sectionnumber",
        "planlotarea","planlotareaunits","startdate","enddate","lastupdate","msoid",
        "centroidid","shapeuuid","changetype","lotidstring","processstate","urbanity",
        "Shape__Length","Shape__Area","cadid","createdate","modifieddate"
    ]
    seen = set()
    rows = []
    for k in preferred:
        if k in props:
            rows.append((k, props.get(k, ""))); seen.add(k)
    for k in sorted(props.keys()):
        if k not in seen:
            rows.append((k, props.get(k, "")))

    parts = ["<center><table>",
             "<tr><th colspan='2' align='center'><em>Attributes</em></th></tr>"]
    for i, (k, v) in enumerate(rows):
        bg = '#E3E3F3' if i % 2 == 0 else ''
        val = "" if v is None else str(v)
        parts.append(f'<tr bgcolor="{bg}"><th>{k}</th><td>{val}</td></tr>')
    parts.append("</table></center>")
    return "".join(parts)

def _close_ring(r: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if r and r[0] != r[-1]:
        return r + [r[0]]
    return r

def _as_positions(seq) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for pt in seq or []:
        if isinstance(pt, (list, tuple)) and pt and isinstance(pt[0], (int, float)):
            x = float(pt[0]); y = float(pt[1]) if len(pt) > 1 else 0.0
            out.append((x, y))
    return out

def _iter_polygons_with_holes(geom: Dict) -> Iterable[Tuple[List[Tuple[float,float]], List[List[Tuple[float,float]]]]]:
    """Yield (outer_ring, inner_rings[]) from Polygon/MultiPolygon (not closed)."""
    if not geom:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Polygon" and isinstance(c, list) and c:
        outer = _as_positions(c[0])
        inners = [_as_positions(r) for r in c[1:]]
        if outer:
            yield outer, [r for r in inners if r]
    elif t == "MultiPolygon" and isinstance(c, list):
        for poly in c:
            if not poly: continue
            outer = _as_positions(poly[0])
            inners = [_as_positions(r) for r in poly[1:]]
            if outer:
                yield outer, [r for r in inners if r]
    # ignore non-polygons

def save_kml(
    feature_collection: Dict,
    out_dir: str,
    filename: str = "parcels.kml",
    state: Optional[str] = None,
    colour: Optional[str] = None,
    line_width: float = 1.5,
    folder_name: Optional[str] = None,   # <-- added
) -> str:
    """
    Save a GeoJSON FeatureCollection to a styled KML file with popups.
    - ONE folder (named `folder_name`, if provided)
    - Group ALL features by lotidstring (fallback: label -> lotplan -> 'parcel')
    - ONE MultiGeometry placemark per group (so the sidebar shows one row)
    """
    os.makedirs(out_dir, exist_ok=True)
    kml = simplekml.Kml()

    # Create a parent folder if requested
    parent = kml.newfolder(name=folder_name) if folder_name else kml

    poly_colour = colour or (STATE_COLOURS.get(state.upper(), DEFAULT_STYLE["poly_color"]) if state else DEFAULT_STYLE["poly_color"])
    line_colour = DEFAULT_STYLE["line_color"]

    # Group features
    groups: Dict[str, Dict[str, any]] = {}
    for f in (feature_collection.get("features", []) or []):
        props = (f.get("properties", {}) or {}).copy()
        if state:
            props["state"] = state
        key = props.get("lotidstring") or props.get("label") or props.get("lotplan") or "parcel"
        groups.setdefault(key, {"props": props, "geoms": []})
        groups[key]["geoms"].append(f.get("geometry", {}) or {})

    # One MultiGeometry placemark per lot
    for key, bundle in groups.items():
        props = bundle["props"]
        name = key
        desc_html = _feature_popup_html(props)

        mg = parent.newmultigeometry(name=name, description=desc_html)
        mg.snippet = simplekml.Snippet("", maxlines=0)  # sidebar: name only
        mg.style.polystyle = simplekml.PolyStyle(color=poly_colour, fill=1, outline=1)
        mg.style.linestyle = simplekml.LineStyle(color=line_colour, width=line_width)

        for geom in bundle["geoms"]:
            for outer, inners in _iter_polygons_with_holes(geom):
                if not outer:
                    continue
                poly = mg.newpolygon()
                poly.outerboundaryis = _close_ring(outer)
                for hole in inners:
                    hole = _close_ring(hole)
                    if hole:
                        poly.innerboundaryis.append(hole)

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
