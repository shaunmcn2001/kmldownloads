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

def _iter_outer_rings(geom: Dict) -> Iterable[List[Tuple[float, float]]]:
    """Yield outer rings as (lon,lat) lists from Polygon/MultiPolygon; ignore holes."""
    if not geom: return
    gtype = geom.get("type"); coords = geom.get("coordinates")

    def as_positions(seq):
        out: List[Tuple[float, float]] = []
        for pt in seq or []:
            if isinstance(pt, (list, tuple)) and pt and isinstance(pt[0], (int, float)):
                x = float(pt[0]); y = float(pt[1]) if len(pt) > 1 else 0.0
                out.append((x, y))
        return out

    if gtype == "Polygon":
        if isinstance(coords, list) and coords:
            ring = as_positions(coords[0]); 
            if ring: yield ring
    elif gtype == "MultiPolygon":
        if isinstance(coords, list):
            for poly in coords:
                ring = as_positions(poly[0] if (isinstance(poly, list) and poly) else [])
                if ring: yield ring
    # else ignore non-polygons

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
    - Polygon/MultiPolygon supported (outer ring only)
    - Sidebar shows ONLY the placemark name (no description preview)
    - Balloon popup shows the full Attributes table
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
        if state: props["state"] = state

        name = (
            props.get("label") or props.get("lotidstring") or props.get("lotplan")
            or props.get("planparcel") or props.get("plan") or "parcel"
        )
        desc_html = _feature_popup_html(props)

        wrote_any = False
        for ring in _iter_outer_rings(geom):
            if not ring: continue
            if ring[0] != ring[-1]:
                ring = ring + [ring[0]]  # close ring

            p = kml.newpolygon(name=name)
            p.outerboundaryis = ring
            p.style.polystyle = polystyle
            p.style.linestyle = linestyle

            # 🔑 Sidebar shows only the name; popup shows the table
            p.description = desc_html
            p.snippet = simplekml.Snippet("", maxlines=0)  # hide snippet preview in side panel

            wrote_any = True
        if not wrote_any:
            continue

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
