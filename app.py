# app/main.py
import os, tempfile, logging, zipfile, csv, datetime as dt
from io import BytesIO
from typing import List, Optional, Dict, Any, Tuple, Literal

from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .arcgis import fetch_parcel_geojson, fetch_landtypes_intersecting_envelope
from .rendering import to_shapely_union, bbox_3857, prepare_clipped_shapes, make_geotiff_rgba
from .colors import color_from_code
from .kml import build_kml, write_kmz

logging.basicConfig(level=logging.INFO)
app = FastAPI(
    title="QLD Land Types → GeoTIFF + KMZ (Unified)",
    description="Enter a QLD Lot/Plan; download GeoTIFF, clickable KMZ, or both. Single or bulk from one box.",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],  # POST for combined/bulk
    allow_headers=["*"],
)

# ───────────────────────────────────────── Helpers ─────────────────────────────────────────

def rgb_to_hex(rgb):
    r, g, b = rgb
    return "#{:02x}{:02x}{:02x}".format(r, g, b)

def _sanitize_filename(s: str) -> str:
    base = "".join(c for c in s.strip() if c.isalnum() or c in ("_", "-", ".", " ")).strip()
    return base or "download"

def _build_kml_compat(clipped, folder_label: str):
    """
    Call build_kml with whatever signature your local .kml module supports.
    Tries several common kwarg names; falls back to no label if none match.
    """
    for kw in ("folder_name", "doc_name", "document_name", "name"):
        try:
            return build_kml(clipped, color_fn=color_from_code, **{kw: folder_label})
        except TypeError as e:
            msg = str(e)
            if "unexpected keyword argument" in msg or "got multiple values for" in msg:
                continue
            raise
    return build_kml(clipped, color_fn=color_from_code)

def _render_one_tiff_and_meta(lotplan: str, max_px: int) -> Tuple[bytes, Dict[str, Any]]:
    """
    Builds a GeoTIFF for a single lot/plan and returns (tiff_bytes, meta).
    Meta includes simple bounds and total area (ha).
    """
    lotplan = lotplan.strip().upper()
    parcel_fc = fetch_parcel_geojson(lotplan)
    parcel_union = to_shapely_union(parcel_fc)
    env = bbox_3857(parcel_union)
    lt_fc = fetch_landtypes_intersecting_envelope(env)
    clipped = prepare_clipped_shapes(parcel_fc, lt_fc)
    if not clipped:
        raise HTTPException(status_code=404, detail="No Land Types intersect this parcel.")

    tmpdir = tempfile.mkdtemp(prefix="geotiff_")
    out_path = os.path.join(tmpdir, f"{lotplan}_landtypes.tif")
    try:
        result = make_geotiff_rgba(clipped, out_path, max_px=max_px)
        with open(out_path, "rb") as f:
            tiff_bytes = f.read()
    finally:
        try:
            if os.path.exists(out_path): os.remove(out_path)
            if os.path.isdir(tmpdir): os.rmdir(tmpdir)
        except Exception:
            pass

    west, south, east, north = parcel_union.bounds
    total_area_ha = sum(float(a_ha) for _, _, _, a_ha in clipped)
    meta = {
        "lotplan": lotplan,
        "bounds_epsg4326": [west, south, east, north],
        "area_ha_total": total_area_ha,
        **{k: v for k, v in result.items() if k != "path"}
    }
    return tiff_bytes, meta

def _render_one_kmz_and_meta(lotplan: str, simplify_tolerance: float = 0.0) -> Tuple[bytes, Dict[str, Any]]:
    """
    Builds a KMZ (clickable) and returns (kmz_bytes, meta).
    """
    lotplan = lotplan.strip().upper()
    parcel_fc = fetch_parcel_geojson(lotplan)
    parcel_union = to_shapely_union(parcel_fc)
    env = bbox_3857(parcel_union)
    lt_fc = fetch_landtypes_intersecting_envelope(env)
    clipped = prepare_clipped_shapes(parcel_fc, lt_fc)
    if not clipped:
        raise HTTPException(status_code=404, detail="No Land Types intersect this parcel.")

    if simplify_tolerance and simplify_tolerance > 0:
        simplified = []
        for geom4326, code, name, area_ha in clipped:
            g2 = geom4326.simplify(simplify_tolerance, preserve_topology=True)
            if not g2.is_empty:
                simplified.append((g2, code, name, area_ha))
        clipped = simplified or clipped

    kml = _build_kml_compat(clipped, f"QLD Land Types – {lotplan}")

    tmpdir = tempfile.mkdtemp(prefix="kmz_")
    out_path = os.path.join(tmpdir, f"{lotplan}_landtypes.kmz")
    try:
        write_kmz(kml, out_path)
        with open(out_path, "rb") as f:
            kmz_bytes = f.read()
    finally:
        try:
            if os.path.exists(out_path): os.remove(out_path)
            if os.path.isdir(tmpdir): os.rmdir(tmpdir)
        except Exception:
            pass

    west, south, east, north = parcel_union.bounds
    total_area_ha = sum(float(a_ha) for _, _, _, a_ha in clipped)
    meta = {
        "lotplan": lotplan,
        "bounds_epsg4326": [west, south, east, north],
        "area_ha_total": total_area_ha,
    }
    return kmz_bytes, meta

# Global exception handler to surface actual errors as JSON
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error during %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": str(exc)}
    )

# ───────────────────────────────────────── UI ─────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>QLD Land Types → GeoTIFF + KMZ (Unified)</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
  <style>
    :root { --bg:#0b1220; --card:#121a2b; --text:#e8eefc; --muted:#9fb2d8; --accent:#6aa6ff; }
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif}
    .wrap{max-width:1100px;margin:28px auto;padding:0 16px}.card{background:var(--card);border:1px solid #1f2a44;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.25);padding:18px}
    h1{margin:4px 0 10px;font-size:26px} p{margin:0 0 14px;color:var(--muted)} label{display:block;margin:10px 0 6px;color:var(--muted);font-size:14px}
    input[type=text],input[type=number],textarea,select{width:100%;padding:10px 12px;border-radius:12px;border:1px solid #2b3960;background:#0e1526;color:var(--text)}
    textarea{min-height:110px;resize:vertical}
    .row{display:flex;gap:12px;flex-wrap:wrap}.row > *{flex:1 1 200px}.btns{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}
    button,.ghost{appearance:none;border:0;border-radius:12px;padding:10px 14px;font-weight:600;cursor:pointer}
    button.primary{background:var(--accent);color:#071021} a.ghost{color:var(--accent);text-decoration:none;border:1px solid #294a86;background:#0d1730}
    .note{margin-top:8px;font-size:13px;color:#89a3d6} #map{height:520px;border-radius:14px;margin-top:14px;border:1px solid #203055}
    .out{margin-top:12px;border-top:1px solid #203055;padding-top:10px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;white-space:pre-wrap}
    .badge{display:inline-block;padding:.2rem .5rem;border-radius:999px;background:#11204a;color:#9fc1ff;font-size:12px;margin-left:8px}
    .chip{display:inline-flex;align-items:center;gap:6px;padding:.2rem .6rem;border-radius:999px;background:#11204a;color:#9fc1ff;font-size:12px;margin-left:8px}
    .muted{color:#9fb2d8}
  </style>
</head><body>
  <div class="wrap"><div class="card">
    <h1>QLD Land Types <span class="badge">EPSG:4326</span> <span id="mode" class="chip">Mode: Single</span></h1>
    <p>Paste one or many <strong>Lot / Plan</strong> codes. We auto-detect single vs bulk (ZIP) and use your chosen format.</p>

    <div class="row">
      <div style="flex: 2 1 420px;">
        <label for="items">Lot / Plan (single OR multiple — new line, comma, or semicolon separated)</label>
        <textarea id="items" placeholder="13SP181800
1RP12345
2RP54321"></textarea>
        <div class="muted" id="parseinfo">Detected 0 items.</div>
      </div>
      <div>
        <label for="fmt">Export format</label>
        <select id="fmt">
          <option value="tiff" selected>GeoTIFF</option>
          <option value="kmz">KMZ (clickable)</option>
          <option value="both">Both (ZIP)</option>
        </select>

        <label for="name">Name (single) or Prefix (bulk)</label>
        <input id="name" type="text" placeholder="e.g. UpperCoomera_13SP181800 or Job_4021" />

        <label for="maxpx">Max raster dimension (px) for GeoTIFF</label>
        <input id="maxpx" type="number" min="256" max="8192" value="4096" />

        <label for="simp">KMZ simplify tolerance (deg) <span class="muted">(try 0.00005 ≈ 5 m)</span></label>
        <input id="simp" type="number" step="0.00001" min="0" max="0.001" value="0" />
      </div>
    </div>

    <div class="btns">
      <button class="primary" id="btn-export">Export</button>
      <a class="ghost" id="btn-json" href="#">Preview JSON (single)</a>
      <a class="ghost" id="btn-load" href="#">Load on Map (single)</a>
    </div>

    <div class="note">API docs: <a href="/docs">/docs</a>.  JSON/Map actions are enabled only when exactly one code is provided.</div>
    <div id="map"></div><div id="out" class="out"></div>
  </div></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
  <script>
    const $items = document.getElementById('items'), $fmt = document.getElementById('fmt'),
          $name = document.getElementById('name'), $max = document.getElementById('maxpx'),
          $simp = document.getElementById('simp'), $mode = document.getElementById('mode'),
          $out = document.getElementById('out'), $parseinfo = document.getElementById('parseinfo'),
          $btnExport = document.getElementById('btn-export'), $btnJson = document.getElementById('btn-json'),
          $btnLoad = document.getElementById('btn-load');

    function normText(s){ return (s || '').trim(); }
    function normLot(s){ return (s || '').trim().toUpperCase(); }
    function parseItems(text){
      const raw = (text || '').split(/\\r?\\n|,|;/);
      const clean = raw.map(s => s.trim().toUpperCase()).filter(Boolean);
      const seen = new Set(); const out = [];
      for(const v of clean){ if(!seen.has(v)){ seen.add(v); out.push(v); } }
      return out;
    }

    // Map init
    const map = L.map('map', { zoomControl: true });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '&copy; OpenStreetMap' }).addTo(map);
    map.setView([-23.5, 146.0], 5);
    let parcelLayer=null, ltLayer=null;
    function styleForCode(code, colorHex){ return { color:'#0c1325', weight:1, fillColor:colorHex, fillOpacity:0.6 }; }
    function clearLayers(){ if(parcelLayer){ map.removeLayer(parcelLayer); parcelLayer=null; } if(ltLayer){ map.removeLayer(ltLayer); ltLayer=null; } }

    function updateMode(){
      const items = parseItems($items.value);
      const n = items.length;
      const dupInfo = (normText($items.value) && n === 0) ? " (duplicates/invalid removed)" : "";
      $parseinfo.textContent = `Detected ${n} item${n===1?'':'s'}.` + dupInfo;

      if (n === 1){
        $mode.textContent = "Mode: Single";
        $btnJson.classList.remove('disabled'); $btnJson.style.pointerEvents='auto'; $btnJson.style.opacity='1';
        $btnLoad.classList.remove('disabled'); $btnLoad.style.pointerEvents='auto'; $btnLoad.style.opacity='1';
      } else {
        $mode.textContent = `Mode: Bulk (${n})`;
        $btnJson.classList.add('disabled'); $btnJson.style.pointerEvents='none'; $btnJson.style.opacity='.5';
        $btnLoad.classList.add('disabled'); $btnLoad.style.pointerEvents='none'; $btnLoad.style.opacity='.5';
      }
    }

    async function downloadBlobAs(res, filename){
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    }

    // Vector preview (single only)
    function mkVectorUrl(lotplan){ return `/vector?lotplan=${encodeURIComponent(lotplan)}`; }

    async function loadVector(){
      const items = parseItems($items.value);
      if (items.length !== 1){ $out.textContent = 'Provide exactly one Lot/Plan to load map.'; return; }
      const lot = items[0];
      $out.textContent = 'Loading vector data…';
      try{
        const res = await fetch(mkVectorUrl(lot)); const data = await res.json();
        if (data.error){ $out.textContent = 'Error: ' + data.error; return; }
        clearLayers();
        parcelLayer = L.geoJSON(data.parcel, { style: { color: '#ffcc00', weight:2, fillOpacity:0 } }).addTo(map);
        ltLayer = L.geoJSON(data.landtypes, { style: f => styleForCode(f.properties.code, f.properties.color_hex),
          onEachFeature: (feature, layer) => {
            const p = feature.properties || {};
            const html = `<b>${p.name || 'Unknown'}</b><br/>Code: <code>${p.code || 'UNK'}</code><br/>Area: ${(p.area_ha ?? 0).toFixed(2)} ha`;
            layer.bindPopup(html);
          }}).addTo(map);
        const b = data.bounds4326; if (b){ map.fitBounds([[b.south, b.west],[b.north, b.east]], { padding:[20,20] }); }
        $out.textContent = JSON.stringify({ lotplan: data.lotplan, legend: data.legend, bounds4326: data.bounds4326 }, null, 2);
      }catch(err){ $out.textContent = 'Network error: ' + err; }
    }

    // JSON preview (single only)
    async function previewJson(){
      const items = parseItems($items.value);
      if (items.length !== 1){ $out.textContent = 'Provide exactly one Lot/Plan for JSON preview.'; return; }
      const lot = items[0];
      $out.textContent='Requesting JSON summary…';
      try{
        const url = `/export?lotplan=${encodeURIComponent(lot)}&max_px=${encodeURIComponent(($max.value || '4096').trim())}&download=false`;
        const res = await fetch(url); const txt = await res.text();
        try{ const data = JSON.parse(txt); $out.textContent = JSON.stringify(data, null, 2);}catch{ $out.textContent = `Error ${res.status}: ${txt}`; }
      }catch(err){ $out.textContent = 'Network error: ' + err; }
    }

    // Unified Export handler (single or bulk)
    async function exportAny(){
      const items = parseItems($items.value);
      if (!items.length){ $out.textContent = 'Enter at least one Lot/Plan.'; return; }
      const fmt = $fmt.value;
      const max_px = parseInt($max.value || '4096', 10);
      const simp = parseFloat($simp.value || '0') || 0;
      const name = normText($name.value) || null;

      const body = { format: fmt, max_px: max_px, simplify_tolerance: simp };
      if (items.length === 1){
        body.lotplan = items[0];
        if (name) body.filename = name;
      } else {
        body.lotplans = items;
        if (name) body.filename_prefix = name;
      }

      $out.textContent = items.length === 1 ? 'Exporting…' : `Exporting ${items.length} items…`;
      try{
        const res = await fetch('/export/any', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
        if (!res.ok){ const txt = await res.text(); $out.textContent = `Error ${res.status}: ${txt}`; return; }

        const disp = res.headers.get('content-disposition') || '';
        const m = /filename="([^"]+)"/i.exec(disp);
        let dl = m ? m[1] : `export_${Date.now()}`;
        if (items.length > 1 && name && !dl.startsWith(name)) dl = `${name}_${dl}`;
        await downloadBlobAs(res, dl);
        $out.textContent = 'Download complete.';
      }catch(err){ $out.textContent = 'Network error: ' + err; }
    }

    // Wire UI
    $items.addEventListener('input', updateMode);
    $btnLoad.addEventListener('click', (e)=>{ e.preventDefault(); loadVector(); });
    $btnJson.addEventListener('click', (e)=>{ e.preventDefault(); previewJson(); });
    $btnExport.addEventListener('click', (e)=>{ e.preventDefault(); exportAny(); });

    updateMode();
    setTimeout(()=>{ $items.focus(); }, 50);
  </script>
</body></html>"""

# ───────────────────────────────────────── Health ─────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}

# ───────────────────────────────────────── Existing endpoints (kept) ─────────────────────────────────────────

@app.get("/export")
def export_geotiff(
    lotplan: str = Query(..., description="QLD Lot/Plan, e.g. 13DP1246224 or 13SP181800"),
    max_px: int = Query(4096, ge=256, le=8192, description="Max raster dimension (px)"),
    download: bool = Query(True, description="Return file download (True) or JSON summary (False)"),
    filename: Optional[str] = Query(None, description="Custom file name for the TIFF (no extension)"),
):
    try:
        lotplan = lotplan.strip().upper()
        parcel_fc = fetch_parcel_geojson(lotplan)
        parcel_union = to_shapely_union(parcel_fc)
        env = bbox_3857(parcel_union)
        lt_fc = fetch_landtypes_intersecting_envelope(env)
        clipped = prepare_clipped_shapes(parcel_fc, lt_fc)
        if not clipped:
            raise HTTPException(status_code=404, detail="No Land Types intersect this parcel.")

        tmpdir = tempfile.mkdtemp(prefix="geotiff_")
        out_path = os.path.join(tmpdir, f"{lotplan}_landtypes.tif")
        result = make_geotiff_rgba(clipped, out_path, max_px=max_px)

        if download:
            if filename:
                dl = _sanitize_filename(filename)
                if not dl.lower().endswith(".tif"): dl += ".tif"
            else:
                dl = os.path.basename(out_path)
            return FileResponse(out_path, media_type="image/tiff", filename=dl)
        else:
            result_public = {k: v for k, v in result.items() if k != "path"}
            return JSONResponse({"lotplan": lotplan, **result_public})
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("Export error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/vector")
def vector_geojson(lotplan: str = Query(..., description="QLD Lot/Plan")):
    try:
        lotplan = lotplan.strip().upper()
        parcel_fc = fetch_parcel_geojson(lotplan)
        parcel_union = to_shapely_union(parcel_fc)
        env = bbox_3857(parcel_union)
        lt_fc = fetch_landtypes_intersecting_envelope(env)
        clipped = prepare_clipped_shapes(parcel_fc, lt_fc)
        if not clipped:
            return JSONResponse({"error": "No Land Types intersect this parcel."}, status_code=404)

        features = []
        legend_map = {}
        from shapely.geometry import mapping as shp_mapping
        for geom4326, code, name, area_ha in clipped:
            color_rgb = color_from_code(code)
            color_hex = rgb_to_hex(color_rgb)
            features.append({
                "type": "Feature",
                "geometry": shp_mapping(geom4326),
                "properties": {"code": code, "name": name, "area_ha": float(area_ha), "color_hex": color_hex}
            })
            if code not in legend_map:
                legend_map[code] = {"code": code, "name": name, "color_hex": color_hex, "area_ha": 0.0}
            legend_map[code]["area_ha"] += float(area_ha)

        union_bounds = to_shapely_union({
            "type":"FeatureCollection",
            "features":[{"type":"Feature","geometry":f["geometry"],"properties":{}} for f in features]
        }).bounds
        west, south, east, north = union_bounds

        return JSONResponse({
            "lotplan": lotplan,
            "parcel": parcel_fc,
            "landtypes": { "type":"FeatureCollection", "features": features },
            "legend": sorted(legend_map.values(), key=lambda d: (-d["area_ha"], d["code"])),
            "bounds4326": {"west": west, "south": south, "east": east, "north": north}
        })
    except Exception as e:
        logging.exception("Vector export error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export_kmz")
def export_kmz(
    lotplan: str = Query(..., description="QLD Lot/Plan, e.g. 13DP1246224 or 13SP181800"),
    simplify_tolerance: float = Query(0.0, ge=0.0, le=0.001, description="Simplify polygons (deg); try 0.00005 ≈ 5 m"),
    filename: Optional[str] = Query(None, description="Custom file name for KMZ (no extension)"),
):
    try:
        lotplan = lotplan.strip().upper()
        kmz_bytes, _meta = _render_one_kmz_and_meta(lotplan, simplify_tolerance=simplify_tolerance)
        if filename:
            dl = _sanitize_filename(filename)
            if not dl.lower().endswith(".kmz"): dl += ".kmz"
        else:
            dl = f"{lotplan}_landtypes.kmz"

        buf = BytesIO(kmz_bytes)
        headers = {"Content-Disposition": f'attachment; filename="{dl}"'}
        return StreamingResponse(buf, media_type="application/vnd.google-earth.kmz", headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("KMZ export error")
        raise HTTPException(status_code=500, detail=str(e))

# Legacy bulk TIFF endpoint (kept for compatibility with earlier clients)
class BulkRequest(BaseModel):
    lotplans: List[str] = Field(..., description="List of QLD Lot/Plan codes")
    max_px: int = Field(4096, ge=256, le=8192)
    download: bool = Field(True, description="If false, returns JSON list of summaries instead of ZIP")
    filename_prefix: Optional[str] = Field(None, description="Optional prefix for file names inside the ZIP")

@app.post("/export/bulk")
def export_bulk(payload: BulkRequest = Body(...)):
    seen = set()
    lotplans: List[str] = []
    for lp in (lp.strip().upper() for lp in payload.lotplans):
        if not lp: continue
        if lp in seen: continue
        seen.add(lp); lotplans.append(lp)
    if not lotplans:
        raise HTTPException(status_code=400, detail="No valid lotplans provided.")

    if not payload.download:
        out: List[Dict[str, Any]] = []
        for lp in lotplans:
            try:
                _tiff, meta = _render_one_tiff_and_meta(lp, payload.max_px)
                out.append({"lotplan": lp, "ok": True, **{k: v for k, v in meta.items() if k != "path"}})
            except HTTPException as e:
                out.append({"lotplan": lp, "ok": False, "message": e.detail})
            except Exception as e:
                out.append({"lotplan": lp, "ok": False, "message": str(e)})
        return JSONResponse(content=out)

    prefix = _sanitize_filename(payload.filename_prefix) if payload.filename_prefix else None
    zip_buf = BytesIO()
    manifest_rows: List[Dict[str, Any]] = []

    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for lp in lotplans:
            try:
                tiff_bytes, meta = _render_one_tiff_and_meta(lp, payload.max_px)
                name = f"{(prefix+'_') if prefix else ''}{lp}_landtypes.tif"
                zf.writestr(name, tiff_bytes)
                manifest_rows.append({
                    "lotplan": lp, "status": "ok", "file": name,
                    "bounds_epsg4326": meta.get("bounds_epsg4326"), "area_ha_total": meta.get("area_ha_total")
                })
            except HTTPException as e:
                manifest_rows.append({"lotplan": lp, "status": f"error:{e.status_code}", "file": "", "message": e.detail})
            except Exception as e:
                manifest_rows.append({"lotplan": lp, "status": "error:500", "file": "", "message": str(e)})

        mem_csv = BytesIO()
        fieldnames = ["lotplan","status","file","bounds_epsg4326","area_ha_total","message"]
        writer = csv.DictWriter(mem_csv, fieldnames=fieldnames); writer.writeheader()
        for row in manifest_rows:
            for k in fieldnames: row.setdefault(k, "")
            writer.writerow(row)
        zf.writestr("manifest.csv", mem_csv.getvalue())

    zip_buf.seek(0)
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dl_name = f"{(prefix + '_' ) if prefix else ''}landtypes_bulk_{stamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{dl_name}"'}
    return StreamingResponse(zip_buf, media_type="application/zip", headers=headers)

# ───────────────────────────────────────── Unified single/bulk endpoint ─────────────────────────────────────────

class ExportAnyRequest(BaseModel):
    lotplan: Optional[str] = Field(None, description="Single QLD Lot/Plan")
    lotplans: Optional[List[str]] = Field(None, description="Multiple QLD Lot/Plan codes")
    max_px: int = Field(4096, ge=256, le=8192)
    format: Literal["tiff","kmz","both"] = "tiff"
    filename: Optional[str] = Field(None, description="Custom filename for single-file responses (no extension)")
    filename_prefix: Optional[str] = Field(None, description="Prefix for files inside ZIP when multiple outputs")
    simplify_tolerance: float = Field(0.0, ge=0.0, le=0.001, description="Simplify polygons for KMZ")

@app.post("/export/any")
def export_any(payload: ExportAnyRequest = Body(...)):
    # Normalize the list of lotplans
    items: List[str] = []
    if payload.lotplans:
        seen = set()
        for lp in (lp.strip().upper() for lp in payload.lotplans):
            if not lp: continue
            if lp in seen: continue
            seen.add(lp); items.append(lp)
    if payload.lotplan:
        lp = payload.lotplan.strip().upper()
        if lp and lp not in items:
            items.append(lp)

    if not items:
        raise HTTPException(status_code=400, detail="Provide lotplan or lotplans.")

    # Decide whether output is a single file or ZIP
    multi_files = (len(items) > 1) or (payload.format == "both")

    if not multi_files:
        # Single lot + single format → return a single file (no ZIP)
        lp = items[0]
        if payload.format == "tiff":
            tiff_bytes, _meta = _render_one_tiff_and_meta(lp, payload.max_px)
            name = _sanitize_filename(payload.filename) if payload.filename else f"{lp}_landtypes"
            if not name.lower().endswith(".tif"): name += ".tif"
            buf = BytesIO(tiff_bytes)
            headers = {"Content-Disposition": f'attachment; filename="{name}"'}
            return StreamingResponse(buf, media_type="image/tiff", headers=headers)

        if payload.format == "kmz":
            kmz_bytes, _meta = _render_one_kmz_and_meta(lp, simplify_tolerance=payload.simplify_tolerance)
            name = _sanitize_filename(payload.filename) if payload.filename else f"{lp}_landtypes"
            if not name.lower().endswith(".kmz"): name += ".kmz"
            buf = BytesIO(kmz_bytes)
            headers = {"Content-Disposition": f'attachment; filename="{name}"'}
            return StreamingResponse(buf, media_type="application/vnd.google-earth.kmz", headers=headers)

        raise HTTPException(status_code=400, detail="Unsupported format for single export.")

    # Otherwise, ZIP with one/more files per lotplan
    prefix = _sanitize_filename(payload.filename_prefix) if payload.filename_prefix else None
    zip_buf = BytesIO()
    manifest_rows: List[Dict[str, Any]] = []

    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for lp in items:
            row: Dict[str, Any] = {"lotplan": lp}

            # TIFF
            if payload.format in ("tiff","both"):
                try:
                    tiff_bytes, meta = _render_one_tiff_and_meta(lp, payload.max_px)
                    name_tif = f"{(prefix+'_') if prefix else ''}{lp}_landtypes.tif"
                    zf.writestr(name_tif, tiff_bytes)
                    row.update({
                        "status_tiff": "ok",
                        "file_tiff": name_tif,
                        "bounds_epsg4326": meta.get("bounds_epsg4326"),
                        "area_ha_total": meta.get("area_ha_total"),
                    })
                except HTTPException as e:
                    row.update({"status_tiff": f"error:{e.status_code}", "file_tiff": "", "tiff_message": e.detail})
                except Exception as e:
                    row.update({"status_tiff": "error:500", "file_tiff": "", "tiff_message": str(e)})

            # KMZ
            if payload.format in ("kmz","both"):
                try:
                    kmz_bytes, meta2 = _render_one_kmz_and_meta(lp, simplify_tolerance=payload.simplify_tolerance)
                    name_kmz = f"{(prefix+'_') if prefix else ''}{lp}_landtypes.kmz"
                    zf.writestr(name_kmz, kmz_bytes)
                    row.update({
                        "status_kmz": "ok",
                        "file_kmz": name_kmz,
                        "bounds_epsg4326": row.get("bounds_epsg4326", meta2.get("bounds_epsg4326")),
                        "area_ha_total": row.get("area_ha_total", meta2.get("area_ha_total")),
                    })
                except HTTPException as e:
                    row.update({"status_kmz": f"error:{e.status_code}", "file_kmz": "", "kmz_message": e.detail})
                except Exception as e:
                    row.update({"status_kmz": "error:500", "file_kmz": "", "kmz_message": str(e)})

            manifest_rows.append(row)

        # Manifest CSV
        mem_csv = BytesIO()
        fieldnames = [
            "lotplan","status_tiff","file_tiff","tiff_message",
            "status_kmz","file_kmz","kmz_message",
            "bounds_epsg4326","area_ha_total"
        ]
        writer = csv.DictWriter(mem_csv, fieldnames=fieldnames); writer.writeheader()
        for row in manifest_rows:
            for k in fieldnames: row.setdefault(k, "")
            writer.writerow(row)
        zf.writestr("manifest.csv", mem_csv.getvalue())

    zip_buf.seek(0)
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base = f"{prefix+'_' if prefix else ''}landtypes_{payload.format}"
    dl_name = f"{base}_bulk_{stamp}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{dl_name}"'}
    return StreamingResponse(zip_buf, media_type="application/zip", headers=headers)