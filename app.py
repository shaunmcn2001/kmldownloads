import pandas as pd
import json, os, time
from typing import Dict, List
import io
from download import save_kml

try:
    import pydeck as pdk
    import streamlit as st
    pdk.settings.mapbox_api_key = st.secrets.get("MAPBOX_API_KEY", st.secrets.get("MAPBOX_TOKEN", ""))
except Exception:
    pass

import NSW_query
import QLD_query
import SA_query
from download import save_kml

st.set_page_config(page_title="MappingKML", layout="wide")
st.title("MappingKML — Cadastre search & KML export")

with st.sidebar:
    st.header("Search")
    with st.form("search_form"):
        raw_input = st.text_area(
            "Enter inputs",
            height=160,
            placeholder="NSW: 13//DP1242624\nQLD: 1RP164839\nSA: 101//D12345 or 5100/123"
        )
        st.caption("NSW: LOT//PLAN (optional LOT/SECTION//PLAN)  |  QLD: lotplan (e.g. 1RP164839)  |  SA: PARCEL//PLAN or VOLUME/FOLIO.")
        c1, c2, c3 = st.columns(3)
        with c1: use_nsw = st.checkbox("NSW", True, key="use_nsw")
        with c2: use_qld = st.checkbox("QLD", True, key="use_qld")
        with c3: use_sa  = st.checkbox("SA",  False, key="use_sa")
        submitted = st.form_submit_button("Run search", type="primary")

# session state
st.session_state.setdefault("features", [])
st.session_state.setdefault("df", None)
st.session_state.setdefault("errors", [])
st.session_state.setdefault("debug_urls", [])
st.session_state.setdefault("last_center", (-27.5, 153.0))

def _remember_debug(url_or_line: str):
    if url_or_line and url_or_line not in st.session_state["debug_urls"]:
        st.session_state["debug_urls"].append(url_or_line)

def _to_dataframe(features: List[Dict]) -> pd.DataFrame:
    rows = []
    for f in features:
        props = f.get("properties", {}).copy()
        geom = f.get("geometry", {})
        centroid = None
        try:
            if geom.get("type") == "Polygon":
                ring = geom["coordinates"][0]
            elif geom.get("type") == "MultiPolygon":
                ring = geom["coordinates"][0][0]
            else:
                ring = None
            if ring:
                xs = [pt[0] for pt in ring]
                ys = [pt[1] for pt in ring]
                centroid = (sum(xs)/len(xs), sum(ys)/len(ys))
        except Exception:
            centroid = None
        props["_lon"] = centroid[0] if centroid else None
        props["_lat"] = centroid[1] if centroid else None
        rows.append(props)
    df = pd.DataFrame(rows)
    return df

def _pydeck_layer_from_features(features: List[Dict], color=[0, 90, 255, 80]):
    gj = {"type": "FeatureCollection", "features": features}
    layer = pdk.Layer(
        "GeoJsonLayer",
        gj,
        stroked=True,
        filled=True,
        extruded=False,
        wireframe=False,
        get_fill_color=color,
        get_line_color=[30,30,30,180],
        line_width_min_pixels=1,
        pickable=True,
        auto_highlight=True,
    )
    return layer

# New: robust bbox center for any geometry type
def _iter_xy_from_geom(geom):
    """Yield (x,y) pairs from any GeoJSON geometry, flattening nested arrays."""
    if not geom:
        return
    coords = geom.get("coordinates")
    def walk(c):
        if isinstance(c, (list, tuple)) and c and isinstance(c[0], (int, float)):
            x = float(c[0])
            y = float(c[1]) if len(c) > 1 else 0.0
            yield (x, y)
        else:
            for part in (c or []):
                yield from walk(part)
    yield from walk(coords)

def _bbox_center(features):
    """Return (lat, lon) center of bbox across all feature geometries; fallback to default."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for f in features or []:
        geom = (f or {}).get("geometry")
        if not geom:
            continue
        for x, y in _iter_xy_from_geom(geom):
            if x < xmin: xmin = x
            if x > xmax: xmax = x
            if y < ymin: ymin = y
            if y > ymax: ymax = y
    if xmin == float("inf"):
        return (-27.5, 153.0)
    return ((ymin + ymax)/2.0, (xmin + xmax)/2.0)

if submitted and raw_input.strip():
    st.session_state["errors"] = []
    st.session_state["debug_urls"] = []
    collections = []

    with st.spinner("Querying services..."):
        if st.session_state.get("use_nsw"):
            try:
                res = NSW_query.query(raw_input)
                if isinstance(res, tuple):
                    fc, dbg = res
                    for line in dbg: _remember_debug(line)
                else:
                    fc = res
                for f in fc.get("features", []):
                    p = f.setdefault("properties", {})
                    p["source"] = "NSW_Cadastre"
                    p["state"] = "NSW"
                collections.append(fc)
            except Exception as e:
                st.session_state["errors"].append(f"NSW error: {e}")

        if st.session_state.get("use_qld"):
            try:
                res = QLD_query.query(raw_input)
                if isinstance(res, tuple):
                    fc, urls = res
                    for u in urls: _remember_debug(u)
                else:
                    fc = res
                for f in fc.get("features", []):
                    p = f.setdefault("properties", {})
                    p["source"] = "QLD_LPPF"
                    p["state"] = "QLD"
                    # optional: set label if your QLD props include lotplan
                    if "label" not in p and "lotplan" in p:
                        p["label"] = p["lotplan"]
                collections.append(fc)
            except Exception as e:
                st.session_state["errors"].append(f"QLD error: {e}")

        if st.session_state.get("use_sa"):
            try:
                res = SA_query.query(raw_input)
                if isinstance(res, tuple):
                    fc, urls = res
                    for u in urls: _remember_debug(u)
                else:
                    fc = res
                for f in fc.get("features", []):
                    p = f.setdefault("properties", {})
                    p["source"] = "SA_DAP_Parcels"
                    p["state"] = "SA"
                collections.append(fc)
            except Exception as e:
                st.session_state["errors"].append(f"SA error: {e}")

    feats = []
    for fc in collections:
        feats.extend(fc.get("features", []))
    st.session_state["features"] = feats

    df = _to_dataframe(feats)
    st.session_state["df"] = df
    if df is not None and not df.empty and df["_lat"].notna().any() and df["_lon"].notna().any():
        st.session_state["last_center"] = (float(df["_lat"].dropna().iloc[0]), float(df["_lon"].dropna().iloc[0]))

with st.expander("Diagnostics", expanded=False):
    st.write(f"Features: {len(st.session_state.get('features', []))}")
    errs = st.session_state.get("errors", [])
    if errs:
        st.error("Errors during query:")
        for e in errs: st.write("• ", e)
    dbg = st.session_state.get("debug_urls", [])
    if dbg:
        st.markdown("**Query details:**")
        for u in dbg:
            st.code(u, language="text")

feats = st.session_state.get("features", [])
df = st.session_state.get("df", None)

if not feats:
    st.info("Run a search to see parcels.")
else:
    groups = {"NSW": [], "QLD": [], "SA": [], "ALL": []}
    for f in feats:
        s = f.get("properties", {}).get("state")
        if s in groups:
            groups[s].append(f)
        else:
            groups["ALL"].append(f)

    colors = {
        "NSW": [0, 90, 255, 80],
        "QLD": [0, 200, 0, 80],
        "SA":  [255, 170, 0, 80],
        "ALL": [120, 120, 120, 80],
    }

    layers = []
    for name, flist in groups.items():
        if flist:
            layers.append(_pydeck_layer_from_features(flist, colors[name]))
    if not layers:
        layers = [_pydeck_layer_from_features(feats, [120,120,120,80])]

    if df is not None and not df.empty and df["_lat"].notna().any() and df["_lon"].notna().any():
        lat = float(df["_lat"].dropna().iloc[0]); lon = float(df["_lon"].dropna().iloc[0])
    else:
        lat, lon = _bbox_center(feats)

    view = pdk.ViewState(latitude=lat, longitude=lon, zoom=10)
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_style="mapbox://styles/mapbox/light-v9",
        tooltip={"html": "{state} – {label}", "style": {"backgroundColor": "white", "color": "black"}}
    )
    st.pydeck_chart(deck)

st.markdown("### Download")

PRESET_HEX = {
    "Subjects (#009FDF)": "#009FDF",
    "Quotes (#A23F97)":   "#A23F97",
    "Sales (#FF0000)":    "#FF0000",
    "For Sales (#ED7D31)": "#ED7D31",
    "Subject Old (#1859A9)": "##859A9",
    "Custom…":            None
}

colA, colB, colC = st.columns([2,1,1])
with colA:
    folder = st.text_input("Folder name", value="parcels_export")
    preset = st.selectbox("Fill colour preset", list(PRESET_HEX.keys()), index=0)
with colB:
    alpha_pct = st.number_input("Opacity (0–100)", min_value=0, max_value=100, value=40, step=5)
    alpha = round((alpha_pct / 100) * 255)
with colC:
    line_width = st.number_input("Border width (px)", min_value=1.0, max_value=10.0, value=3.0, step=0.5)

custom_hex = None
if preset == "Custom…":
    custom_hex = st.text_input("Custom hex (#RRGGBB)", value="#00AAFF")

def _hex_rgb_to_kml_abgr(hex_rgb: str, a: int) -> str:
    h = hex_rgb.lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

selected_hex = custom_hex if preset == "Custom…" else PRESET_HEX[preset]
kml_colour = _hex_rgb_to_kml_abgr(selected_hex, alpha) if selected_hex else None

# Directly provide download if features exist
if st.session_state.get("features"):
    merged_fc = {"type": "FeatureCollection", "features": st.session_state["features"]}
    path = save_kml(
        merged_fc,
        out_dir=folder,
        filename="parcels.kml",
        state=None,
        colour=kml_colour,
        line_width=float(line_width),
        folder_name=folder,
    )

    with open(path, "rb") as fh:
        st.download_button(
            label="Download KML",
            data=fh.read(),
            file_name="parcels.kml",
            mime="application/vnd.google-earth.kml+xml",
        )
else:
    st.info("Run a search to enable KML download.")
