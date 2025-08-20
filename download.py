# ... keep the rest of your file as-is (imports, DEFAULT_STYLE, STATE_COLOURS, helpers)

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
    - ONE Placemark per group using MultiGeometry
    - Polygon + MultiPolygon supported (holes included)
    - Sidebar shows ONLY the placemark name (snippet hidden)
    """
    os.makedirs(out_dir, exist_ok=True)
    kml = simplekml.Kml()

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

    for key, bundle in groups.items():
        props = bundle["props"]
        name = key
        desc_html = _feature_popup_html(props)

        # ✅ Use MultiGeometry placemark (this exists in simplekml)
        mg = kml.newmultigeometry(name=name, description=desc_html)
        mg.snippet = simplekml.Snippet("", maxlines=0)  # sidebar: name only
        mg.style.polystyle = simplekml.PolyStyle(color=poly_colour, fill=1, outline=1)
        mg.style.linestyle = simplekml.LineStyle(color=line_colour, width=line_width)

        # Add all polygons (and holes) to this single placemark
        for geom in bundle["geoms"]:
            t = (geom or {}).get("type")
            c = (geom or {}).get("coordinates")

            if t == "Polygon" and isinstance(c, list) and c:
                outer = [(x, y) for x, y, *rest in c[0]]
                if outer and outer[0] != outer[-1]:
                    outer.append(outer[0])
                poly = mg.newpolygon()
                poly.outerboundaryis = outer
                for hole in c[1:]:
                    inner = [(x, y) for x, y, *rest in hole]
                    if inner and inner[0] != inner[-1]:
                        inner.append(inner[0])
                    if inner:
                        poly.innerboundaryis.append(inner)

            elif t == "MultiPolygon" and isinstance(c, list):
                for polycoords in c:
                    if not polycoords:
                        continue
                    outer = [(x, y) for x, y, *rest in polycoords[0]]
                    if outer and outer[0] != outer[-1]:
                        outer.append(outer[0])
                    poly = mg.newpolygon()
                    poly.outerboundaryis = outer
                    for hole in polycoords[1:]:
                        inner = [(x, y) for x, y, *rest in hole]
                        if inner and inner[0] != inner[-1]:
                            inner.append(inner[0])
                        if inner:
                            poly.innerboundaryis.append(inner)
            # ignore non-polygons

    out_path = os.path.join(out_dir, filename)
    kml.save(out_path)
    return out_path
