
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
    raw_input = st.text_area("Enter Lot/Plan pairs (comma or newline separated)", height=160, placeholder="e.g.\n13//DP1242624\n1RP912949\n101//D12345")
    st.caption("Formats: NSW `LOT//PLAN` (+ optional `LOT/SECTION//PLAN`), SA `PARCEL//PLAN` or `VOLUME/FOLIO` (e.g., `5100/123`).
For QLD, **enter lotidstring only** (e.g., `1RP912949`, `13SP12345`) — one per line or comma-separated.")
    st.subheader("States")
    col1, col2, col3 = st.columns(3)
    with col1:
        use_nsw = st.checkbox("NSW", value=True)
    with col2:
        use_qld = st.checkbox("QLD", value=True)
    with col3:
        use_sa  = st.checkbox("SA",  value=False)

    run = st.button("Run search", type="primary")

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

if run and raw_input.strip():
    collections = []
    errors = []

    with st.spinner("Querying services..."):
        if use_nsw:
            try:
                fc = NSW_query.query(raw_input)
                # annotate state in properties
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "NSW_Cadastre"
                    f["properties"]["state"] = "NSW"
                collections.append(fc)
            except Exception as e:
                errors.append(f"NSW error: {e}")

        if use_qld:
            try:
                fc = QLD_query.query(raw_input)
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "QLD_LPPF"
                    f["properties"]["state"] = "QLD"
                collections.append(fc)
            except Exception as e:
                errors.append(f"QLD error: {e}")

        if use_sa:
            try:
                fc = SA_query.query(raw_input)
                for f in fc.get("features", []):
                    f.setdefault("properties",{})["source"] = "SA_DAP_Parcels"
                    f["properties"]["state"] = "SA"
                collections.append(fc)
            except Exception as e:
                errors.append(f"SA error: {e}")

    feats = _collect_features(collections)
    if len(feats) == 0:
        st.warning("No parcels found. Check your inputs and selected states.")
    else:
        df = _to_dataframe(feats)

        # Map
        # Center on first centroid, fallback to east-coast
        lon = df["_lon"].dropna().iloc[0] if df["_lon"].notna().any() else 153.0
        lat = df["_lat"].dropna().iloc[0] if df["_lat"].notna().any() else -27.5

        # Mixed coloring by state
        color_by_state = {
            "NSW": [0,90,255,80],
            "QLD": [0,200,0,80],
            "SA":  [255,170,0,80],
        }
        features_by_state = {"NSW":[], "QLD":[], "SA":[]}
        for f in feats:
            stt = f.get("properties",{}).get("state","Other")
            if stt in features_by_state:
                features_by_state[stt].append(f)

        layers = []
        for st_name, feats_list in features_by_state.items():
            if feats_list:
                layers.append(_pydeck_layer_from_features(feats_list, color_by_state[st_name]))

        view_state = pdk.ViewState(latitude=lat, longitude=lon, zoom=8)
        r = pdk.Deck(layers=layers, initial_view_state=view_state, map_style="mapbox://styles/mapbox/light-v9", tooltip={"text":"{state}"})
        map_slot.pydeck_chart(r)

        with result_slot:
            st.subheader("Results")
            st.dataframe(df)

            # Download section
            st.markdown("### Download")
            colA, colB = st.columns(2)
            with colA:
                folder = st.text_input("Folder name", value=f"parcels_{int(time.time())}")
                colour_choice = st.selectbox("KML fill colour (by state default unless overridden)", options=["Default (by state)","Blue","Green","Yellow","Red","Purple"])
            with colB:
                alpha = st.slider("Fill opacity", min_value=0, max_value=255, value=125)
                line_width = st.slider("Border width", min_value=1, max_value=5, value=2)

            colour_map = {
                "Blue":   (0,90,255),
                "Green":  (0,200,0),
                "Yellow": (255,200,0),
                "Red":    (220,0,0),
                "Purple": (130,0,180),
            }

            os.makedirs(folder, exist_ok=True)
            merged_fc = {"type":"FeatureCollection","features":feats}

            # If user overrides colour, use one across all states
            colour_hex = None
            if colour_choice != "Default (by state)":
                r,g,b = colour_map[colour_choice]
                a = alpha
                # ABGR for KML
                colour_hex = f"{a:02x}{b:02x}{g:02x}{r:02x}"

            if st.button("Export KML"):
                path = save_kml(merged_fc, out_dir=folder, filename="parcels.kml", state=None if colour_hex else "QLD", colour=colour_hex, line_width=line_width)
                st.success(f"KML saved to: {path}")

            st.caption("KML includes attribute popups and chosen colour/transparency.")
