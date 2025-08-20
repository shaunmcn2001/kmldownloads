# download.py
import os
from typing import Dict, List, Optional, Iterable, Tuple
import simplekml

DEFAULT_STYLE = {
    "line_width": 1.5,
    "line_color": "ffaaaaaa",   # aabbggrr (KML is ABGR)
    "poly_color": "7d00ff00",   # 0x7d opacity + 00ff00 green -> ABGR
}

STATE_COLOURS = {
    "NSW": "7d0000ff",  # semi-blue
    "QLD": "7d00ff00",  # semi-green
    "SA":  "7d00ffff",  # semi-yellow (red+green)
}

def _kml_color_from_rgba(r: int, g: int, b: int, a: int) -> str:
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

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
    """
    Yield (outer_ring, inner_rings[]) pairs from a GeoJSON Polygon/MultiPolygon.
    Rings are (lon,lat) tuples and are NOT closed here.
    """
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
    line_width: float = 1.5
) -> str:
    """
    Save a GeoJSON FeatureCollection to a styled KML file with popups.
    - Groups ALL features by lotidstring (fallback: label -> lotplan -> 'parcel')
    - ONE Placemark per group, even if multiple polygons
    - Polygon + MultiPolygon supported (holes included)
    - Sidebar shows ONLY the placemark name (snippet hidden)
    """
    os.makedirs(out_dir, exist_ok=True)
    kml = simplekml.Kml()

    poly_colour = colour or (STATE_COLOURS.get(state.upper(), DEFAULT_STYLE["poly_color"]) if state else DEFAULT_STYLE["poly_color"])
    line_colour = DEFAULT_STYLE["line_color"]

    # Group features by lotidstring/label/lotplan
    groups: Dict[str, Dict[str, any]] = {}
    for f in (feature_collection.get("features", []) or []):
        props = (f.get("properties", {}) or {}).copy()
        if state: props["state"] = state
        key = props.get("lotidstring") or props.get("label") or props.get("lotplan") or "parcel"
        groups.setdefault(key, {"props": props, "geoms": []})
        groups[key]["geoms"].append(f.get("geometry", {}) or {})

    # Write one placemark per key, attach multiple polygons to it
    for key, bundle in groups.items():
        props = bundle["props"]
        name = key
        desc_html = _feature_popup_html(props)

        pm = kml.newplacemark(name=name, description=desc_html)
        pm.snippet = simplekml.Snippet("", maxlines=0)  # sidebar: name only

        # Style can be set on placemark; child polygons inherit
        pm.style.polystyle = simplekml.PolyStyle(color=poly_colour, fill=1, outline=1)
        pm.style.linestyle = simplekml.LineStyle(color=line_colour, width=line_width)

        for geom in bundle["geoms"]:
            for outer, inners in _iter_polygons_with_holes(geom):
                if not outer:
                    continue
                outer = _close_ring(outer)
                poly = pm.newpolygon()
                poly.outerboundaryis = outer
                for hole in inners:
                    hole = _close_ring(hole)
                    if hole:
                        poly.innerboundaryis.append(hole)

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
