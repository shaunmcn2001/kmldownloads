import pandas as pd
import json, os, time
from typing import Dict, List

try:
    import pydeck as pdk
    import streamlit as st
    # Use MAPBOX_API_KEY if present, else MAPBOX_TOKEN
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
        st.caption(
            "NSW: LOT//PLAN (optional LOT/SECTION//PLAN)  |  QLD: lotplan (e.g. 1RP164839)  |  SA: PARCEL//PLAN or VOLUME/FOLIO."
        )
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

# helper to remember debug info
def _remember_debug(url):
    if url and url not in st.session_state["debug_urls"]:
        st.session_state["debug_urls"].append(url)

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

if submitted and raw_input.strip():
    st.session_state["errors"] = []
    st.session_state["debug_urls"] = []
    collections = []

    with st.spinner("Querying services..."):
        if st.session_state.get("use_nsw"):
            try:
                res = NSW_query.query(raw_input)
                if isinstance(res, tuple):
                    fc, urls = res
                    for u in urls: _remember_debug(u)
                else:
                    fc = res
                for f in fc.get("features", []):
                    f.setdefault("properties", {})["source"] = "NSW_Cadastre"
                    f["properties"]["state"] = "NSW"
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
                    f.setdefault("properties", {})["source"] = "QLD_LPPF"
                    f["properties"]["state"] = "QLD"
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
                    f.setdefault("properties", {})["source"] = "SA_DAP_Parcels"
                    f["properties"]["state"] = "SA"
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
        st.markdown("**Query URLs:**")
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

    def _bbox_center(fs):
        xmin = ymin = float("inf")
        xmax = ymax = float("-inf")
        for f in fs:
            g = f.get("geometry", {})
            if g.get("type") == "Polygon":
                ring = g["coordinates"][0]
                xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
            elif g.get("type") == "MultiPolygon":
                xs = []; ys = []
                for poly in g["coordinates"]:
                    ring = poly[0]
                    xs += [p[0] for p in ring]; ys += [p[1] for p in ring]
            else:
                continue
            if xs and ys:
                xmin = min(xmin, min(xs)); xmax = max(xmax, max(xs))
                ymin = min(ymin, min(ys)); ymax = max(ymax, max(ys))
        if xmin == float("inf"):
            return (-27.5, 153.0)
        return ((ymin + ymax)/2.0, (xmin + xmax)/2.0)

    if df is not None and not df.empty and df["_lat"].notna().any() and df["_lon"].notna().any():
        lat = float(df["_lat"].dropna().iloc[0]); lon = float(df["_lon"].dropna().iloc[0])
    else:
        lat, lon = _bbox_center(feats)

    view = pdk.ViewState(latitude=lat, longitude=lon, zoom=10)
    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_style="mapbox://styles/mapbox/light-v9",
        tooltip={"text":"{state}"}
    )
    st.pydeck_chart(deck)

st.markdown("### Download")

PRESET_HEX = {
    "Subjects (#009FDF)": "#009FDF",
    "Quotes (#A23F97)":   "#A23F97",
    "Sales (#FF0000)":    "#FF0000",
    "For Sales (#ED7D31)": "#ED7D31",
    "Custom…":            None
}

colA, colB, colC = st.columns([2,1,1])
with colA:
    folder = st.text_input("Folder name", value="parcels_export")
    preset = st.selectbox("Fill colour preset", list(PRESET_HEX.keys()), index=0)
with colB:
    alpha = st.number_input("Opacity (0–255)", min_value=0, max_value=255, value=125, step=5)
with colC:
    line_width = st.number_input("Border width (px)", min_value=1.0, max_value=10.0, value=2.0, step=0.5)

custom_hex = None
if preset == "Custom…":
    custom_hex = st.text_input("Custom hex (#RRGGBB)", value="#00AAFF")

def _hex_rgb_to_kml_abgr(hex_rgb: str, a: int) -> str:
    h = hex_rgb.lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

selected_hex = custom_hex if preset == "Custom…" else PRESET_HEX[preset]
kml_colour = _hex_rgb_to_kml_abgr(selected_hex, alpha) if selected_hex else None

if st.button("Export KML"):
    if not st.session_state.get("features"):
        st.warning("No features to export. Run a search first.")
    else:
        merged_fc = {"type":"FeatureCollection","features":st.session_state["features"]}
        path = save_kml(
            merged_fc,
            out_dir=folder,
            filename="parcels.kml",
            state=None,
            colour=kml_colour,
            line_width=float(line_width),
        )
        st.success(f"KML saved to: {path}")
