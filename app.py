
import streamlit as st
import pydeck as pdk
import pandas as pd
import json, os, time
from typing import Dict, List

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
            "Enter Lot/Plan pairs (comma or newline separated)",
            height=160,
            placeholder="e.g.\n13//DP1242624\n1RP912949\n101//D12345"
        )
        st.caption(
            "Formats: NSW `LOT//PLAN` (+ optional `LOT/SECTION//PLAN`), "
            "SA `PARCEL//PLAN` or `VOLUME/FOLIO` (e.g., `5100/123`). "
            "QLD uses `lotplan` (e.g., `1RP164839`)."
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            use_nsw = st.checkbox("NSW", value=True, key="use_nsw")
        with col2:
            use_qld = st.checkbox("QLD", value=True, key="use_qld")
        with col3:
            use_sa  = st.checkbox("SA",  value=False, key="use_sa")

        submitted = st.form_submit_button("Run search", type="primary")

# Initialize session_state containers
if "features" not in st.session_state: st.session_state["features"] = []
if "df" not in st.session_state: st.session_state["df"] = None
if "last_center" not in st.session_state: st.session_state["last_center"] = (-27.5, 153.0)

# Results container
map_slot = st.empty()
result_slot = st.container()

def _collect_features(collections: List[Dict]) -> List[Dict]:
    feats = []
    for fc in collections:
        feats.extend(fc.get("features", []))
    return feats

def _to_dataframe(features: List[Dict]) -> pd.DataFrame:
    rows = []
    for f in features:
        props = f.get("properties",{}).copy()
        geom = f.get("geometry",{})
        centroid = None
        try:
            # quick centroid: average of exterior ring points
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
    # Build GeoJSON Layer
    gj = {"type":"FeatureCollection","features":features}
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

# Run queries only when form is submitted
if submitted and raw_input.strip():
    collections = []
    errors = []

    with st.spinner("Querying services..."):
        if st.session_state.get("use_nsw"):
            try:
                fc = NSW_query.query(raw_input)
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "NSW_Cadastre"
                    f["properties"]["state"] = "NSW"
                collections.append(fc)
            except Exception as e:
                errors.append(f"NSW error: {e}")

        if st.session_state.get("use_qld"):
            try:
                fc = QLD_query.query(raw_input)
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "QLD_LPPF"
                    f["properties"]["state"] = "QLD"
                collections.append(fc)
            except Exception as e:
                errors.append(f"QLD error: {e}")

        if st.session_state.get("use_sa"):
            try:
                fc = SA_query.query(raw_input)
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "SA_DAP_Parcels"
                    f["properties"]["state"] = "SA"
                collections.append(fc)
            except Exception as e:
                errors.append(f"SA error: {e}")

    # Collect and persist features
    feats = []
    for fc in collections:
        feats.extend(fc.get("features", []))
    st.session_state["features"] = feats

    # Create and persist dataframe + map center
    df = _to_dataframe(feats)
    st.session_state["df"] = df
    if df is not None and not df.empty and df["_lat"].notna().any() and df["_lon"].notna().any():
        st.session_state["last_center"] = (float(df["_lat"].dropna().iloc[0]), float(df["_lon"].dropna().iloc[0]))

# Map and results from session_state
feats = st.session_state.get("features", [])
df = st.session_state.get("df", None)

if not feats:
    st.info("Run a search to see parcels.")
else:
    # Split by state for coloured layers
    features_by_state = {"NSW":[], "QLD":[], "SA":[]}
    for f in feats:
        stt = f.get("properties",{}).get("state","Other")
        if stt in features_by_state:
            features_by_state[stt].append(f)

    color_by_state = {
        "NSW": [0,90,255,80],
        "QLD": [0,200,0,80],
        "SA":  [255,170,0,80],
    }
    layers = []
    for st_name, feats_list in features_by_state.items():
        if feats_list:
            layers.append(_pydeck_layer_from_features(feats_list, color_by_state[st_name]))

    lat, lon = st.session_state.get("last_center", (-27.5, 153.0))
    view_state = pdk.ViewState(latitude=lat, longitude=lon, zoom=8)
    r = pdk.Deck(
        layers=layers,
        initial_view_state=view_state,
        map_style="mapbox://styles/mapbox/light-v9",
        tooltip={"text":"{state}"}
    )
    map_slot.pydeck_chart(r)

    with result_slot:
        st.subheader("Results")
        if df is not None:
            st.dataframe(df)

# Download section
st.markdown("### Download")

# Colour presets from photo: Subjects, Quotes, Sales, For Sales
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
    # hex '#RRGGBB' -> KML 'AABBGGRR' (ABGR order)
    h = hex_rgb.lstrip("#")
    r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    return f"{a:02x}{b:02x}{g:02x}{r:02x}"

# Build colour
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
