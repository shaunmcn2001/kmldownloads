
import os
from typing import Dict, List, Optional
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
    keys = ["source","state","lot","plan","lotplan","section","lotidstring","parcel","volume","folio","locality","shire_name","planlabel","lotnumber","sectionnumber","lot_area","shape_Area","st_area(shape)"]
    rows = []
    for k in keys:
        if k in props and props[k] not in (None, "", " "):
            rows.append(f"<tr><th style='text-align:left;padding-right:8px'>{k}</th><td>{props[k]}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"

def save_kml(feature_collection: Dict, out_dir: str, filename: str = "parcels.kml", state: Optional[str] = None, colour: Optional[str] = None, line_width: float = 1.5) -> str:
    """
    Save a GeoJSON FeatureCollection to a styled KML file with popups.
    Returns the file path.
    """
    os.makedirs(out_dir, exist_ok=True)
    kml = simplekml.Kml()

    poly_colour = colour or (STATE_COLOURS.get(state.upper(), DEFAULT_STYLE["poly_color"]) if state else DEFAULT_STYLE["poly_color"])
    line_colour = DEFAULT_STYLE["line_color"]

    polystyle = simplekml.PolyStyle(color=poly_colour, fill=1, outline=0)
    linestyle = simplekml.LineStyle(color=line_colour, width=line_width)

    for feat in feature_collection.get("features", []):
        geom = feat.get("geometry",{})
        props = feat.get("properties",{}).copy()

        if state:
            props["state"] = state

        # KML expects lon,lat; GeoJSON polygons can be MultiPolygon or Polygon
        coords_list = []

        if geom.get("type") == "Polygon":
            coords_list = [geom["coordinates"]]
        elif geom.get("type") == "MultiPolygon":
            coords_list = geom["coordinates"]
        else:
            # ignore non-polygons
            continue

        # folder per feature (optional)
        for poly in coords_list:
            # exterior ring only (poly[0]), holes poly[1:]
            ring = poly[0]
            ls = [(x, y) for x,y in ring]
            p = kml.newpolygon()
            p.outerboundaryis = ls
            p.style.polystyle = polystyle
            p.style.linestyle = linestyle
            p.name = props.get("lotplan") or props.get("lotidstring") or props.get("planparcel") or props.get("plan") or "parcel"
            p.description = _feature_popup_html(props)

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
