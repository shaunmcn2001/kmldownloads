# MappingKML (Refactor)

A streamlined Streamlit app that searches state cadastre services (NSW, QLD, SA), 
maps results, and exports a styled KML.

## Modules
- `app.py` — Streamlit UI (checkboxes for NSW/QLD/SA, bulk input, map & downloads)
- `NSW_query.py`, `QLD_query.py`, `SA_query.py` — state-specific query helpers
- `download.py` — styling + exports (KML) with popups, colours, transparency
- `utils.py` — parsers & helpers

## Quick start
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Input formats
- **NSW**: `LOT//PLAN` (`13//DP1242624`), ranges `1-3//DP131118`, optional section `LOT/SECTION//PLAN` (`13/1//DP1242624`).  
  Also accepts `lotidstring` tokens like `LOT 13 DP1242624`.
- **QLD**: **lotidstring only** (e.g., `1RP912949`, `13SP12345`) — bulk supported; one per line or comma-separated.
- **SA**: `PARCEL//PLAN` (`101//D12345`). Also supports `VOLUME/FOLIO` when provided.
- Multiple entries separated by commas or new lines.

## Notes
- Services used are public ArcGIS REST map layers. If a server is down or throttled,
  queries may return partial results.
- KML export includes styled polygons and a popup with key attributes.
