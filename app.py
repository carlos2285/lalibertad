# app.py
import os, json, glob, math
import streamlit as st
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

# =============================
# Config & estilo
# =============================
st.set_page_config(page_title="Dashboard Territorio", layout="wide")

# Un poco de CSS para que se vea más “dashboard”
st.markdown("""
<style>
/* cards más limpias */
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px;}
.stMetric {background: rgba(255,255,255,0.03); border-radius: 12px; padding: 0.8rem 1rem;}
.stButton>button, .stDownloadButton>button {border-radius: 10px; padding: .6rem .9rem;}
[data-testid="stSidebar"] {min-width: 340px;}
</style>
""", unsafe_allow_html=True)

# pydeck opcional (para polígonos/layers avanzadas)
try:
    import pydeck as pdk
    _HAS_PYDECK = True
except Exception:
    _HAS_PYDECK = False

# =============================
# Utilidades
# =============================
def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    df = df.copy(); df.columns = [str(c).strip() for c in df.columns]; return df

def to_lower_set(cols) -> set: return set([str(c).strip().lower() for c in cols])

@st.cache_data(show_spinner=False)
def load_excel_first_sheet(path: str) -> Tuple[pd.DataFrame, List[str]]:
    xls = pd.ExcelFile(path)
    first = xls.sheet_names[0]
    df = pd.read_excel(path, sheet_name=first)
    return df, xls.sheet_names

def auto_glob(patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits: return hits[0]
    return None

def low_card_cats(df: pd.DataFrame, max_unique=60) -> List[str]:
    out=[]
    for c in df.columns:
        nun = df[c].nunique(dropna=True)
        if nun<=max_unique and (df[c].dtype=='object' or pd.api.types.is_bool_dtype(df[c]) or nun<=20):
            out.append(c)
    return out

def guess_lat_lon(df: pd.DataFrame):
    lat_candidates = ["lat","latitude","y","p002__latitude","latitud","coord_y","y_wgs84"]
    lon_candidates = ["lon","lng","longitude","x","p002__longitude","longitud","coord_x","x_wgs84"]
    cols_lower = {str(c).lower(): c for c in df.columns}
    lat = next((cols_lower[c] for c in lat_candidates if c in cols_lower), None)
    lon = next((cols_lower[c] for c in lon_candidates if c in cols_lower), None)
    return lat, lon

def coerce_decimal(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce")

def parse_codebook(df_cb: pd.DataFrame) -> Dict[str, Dict]:
    meta: Dict[str, Dict] = {}
    if df_cb is None or df_cb.empty: return meta
    cb = df_cb.copy(); cb.columns=[str(c).strip() for c in cb.columns]
    lc = {c.lower(): c for c in cb.columns}

    var_col = next((lc[k] for k in ["variable","var","nombre","campo","name"] if k in lc), None)
    type_col = next((lc[k] for k in ["tipo","type","data_type","clase","class"] if k in lc), None)
    value_col = next((lc[k] for k in ["valor","value","code","código","codigo","option_value"] if k in lc), None)
    label_col = next((lc[k] for k in ["etiqueta","label","meaning","categoria","categoría","option_label"] if k in lc), None)
    opciones_col = next((lc[k] for k in ["opciones","categorias","categorías","levels","choices"] if k in lc), None)

    if var_col and (value_col and label_col):
        for var, g in cb.groupby(var_col, dropna=True):
            var = str(var).strip()
            mapping={}
            for _, row in g.iterrows():
                code=row.get(value_col); lab=row.get(label_col)
                if pd.isna(code) or str(code).strip()=="": continue
                mapping[str(code)] = str(lab) if not pd.isna(lab) else str(code)
            vtype=None
            if type_col and not g[type_col].isna().all():
                vtype=str(g[type_col].dropna().iloc[0]).strip().lower()
            meta[var]={"type":vtype,"label":None,"map":mapping,"raw_type":vtype}

    if var_col and opciones_col:
        for _, row in cb.iterrows():
            var=row.get(var_col); var=None if pd.isna(var) else str(var).strip()
            if not var: continue
            opts=row.get(opciones_col)
            if pd.isna(opts): continue
            mapping={}
            for piece in str(opts).replace(",", ";").split(";"):
                piece=piece.strip()
                if not piece: continue
                if "=" in piece: k,v=piece.split("=",1)
                elif ":" in piece: k,v=piece.split(":",1)
                else: continue
                mapping[str(k).strip()]=str(v).strip()
            vtype=None
            if type_col and not pd.isna(row.get(type_col)):
                vtype=str(row.get(type_col)).strip().lower()
            meta.setdefault(var,{"type":vtype,"label":None,"map":{}, "raw_type":vtype})
            meta[var]["map"]={**meta[var]["map"], **mapping}

    if var_col and type_col:
        for _, row in cb.iterrows():
            var=row.get(var_col); var=None if pd.isna(var) else str(var).strip()
            if not var: continue
            vtype=row.get(type_col)
            if pd.isna(vtype): continue
            vtype=str(vtype).strip().lower()
            meta.setdefault(var,{"type":None,"label":None,"map":{}, "raw_type":None})
            meta[var]["type"]=meta[var]["type"] or vtype
            meta[var]["raw_type"]=meta[var]["raw_type"] or vtype
    return meta

def apply_codebook(df: pd.DataFrame, meta: Dict[str, Dict], apply_labels=True) -> pd.DataFrame:
    if df is None or df.empty or not meta: return df
    out=df.copy(); cols_lower={c.lower(): c for c in out.columns}
    for var_name, info in meta.items():
        col=cols_lower.get(var_name.lower())
        if not col: continue
        vtype=(info or {}).get("type")
        if vtype:
            if any(k in vtype for k in ["num","int","float","double","decimal"]):
                out[col]=pd.to_numeric(out[col], errors="ignore")
            elif any(k in vtype for k in ["date","fecha","time"]):
                try: out[col]=pd.to_datetime(out[col], errors="ignore", infer_datetime_format=True)
                except Exception: pass
        mapping=(info or {}).get("map",{}) or {}
        if apply_labels and mapping:
            raw_col=f"{col}_raw"
            if raw_col not in out.columns: out[raw_col]=out[col]
            out[col]=out[col].apply(lambda x: mapping.get(str(x), x))
    return out

def rank_join_candidates(cols_a, cols_b):
    la, lb = to_lower_set(cols_a), to_lower_set(cols_b)
    inter=list(la.intersection(lb))
    def score(name):
        s=0
        if "id" in name: s+=3
        if "estructura" in name or "struct" in name: s+=2
        if "codigo" in name or "código" in name or "code" in name: s+=1
        if name.endswith("_id") or name.startswith("id_"): s+=2
        return s
    return sorted(inter, key=lambda n: (-score(n), n))

def pick_original_name(df, lower_name):
    for c in df.columns:
        if str(c).strip().lower()==lower_name: return c
    return None

def geojson_center(gj: dict) -> Tuple[float,float]:
    if isinstance(gj, dict) and "bbox" in gj and isinstance(gj["bbox"], (list, tuple)) and len(gj["bbox"])>=4:
        minx, miny, maxx, maxy = gj["bbox"][:4]
        return (miny+maxy)/2.0, (minx+maxx)/2.0
    def _walk(coords, acc):
        if isinstance(coords, (list, tuple)):
            if len(coords)>0 and isinstance(coords[0], (int,float)):
                lon,lat=coords[0],coords[1]
                acc[0]=min(acc[0], lon); acc[1]=min(acc[1], lat)
                acc[2]=max(acc[2], lon); acc[3]=max(acc[3], lat)
            else:
                for c in coords: _walk(c, acc)
    acc=[math.inf, math.inf, -math.inf, -math.inf]
    if isinstance(gj, dict):
        if gj.get("type")=="FeatureCollection":
            for f in gj.get("features", []): _walk(f.get("geometry",{}).get("coordinates", []), acc)
        elif gj.get("type") in ("Polygon","MultiPolygon","LineString","MultiLineString"):
            _walk(gj.get("coordinates", []), acc)
    if acc[0] < acc[2] and acc[1] < acc[3]:
        return (acc[1]+acc[3])/2.0, (acc[0]+acc[2])/2.0
    return 13.494, -89.322  # fallback

def palette(n: int):
    base = [
        [31,120,180], [51,160,44], [227,26,28], [255,127,0], [106,61,154],
        [166,206,227], [178,223,138], [251,154,153], [253,191,111], [202,178,214],
        [255,255,153], [177,89,40]
    ]
    if n <= len(base): return base[:n]
    out = base.copy()
    while len(out)<n:
        out+=base
    return out[:n]

# =============================
# Sidebar: rutas y opciones
# =============================
codebook_default = auto_glob(["data/metadata/Codebook.xlsx", "**/Codebook.xlsx"]) or "data/metadata/Codebook.xlsx"
estr_default     = auto_glob(["data/private/basedarboard.xlsx", "**/basedarboard.xlsx"]) or "data/private/basedarboard.xlsx"
hog_default      = auto_glob(["data/private/hogares.xlsx", "**/hogares.xlsx"]) or "data/private/hogares.xlsx"
lim_default      = auto_glob(["data/gis/areas_intervencion.geojson", "**/*intervencion*.geojson", "**/*limite*.geojson"]) or "data/gis/areas_intervencion.geojson"

st.sidebar.header("Datos de entrada")
codebook_path   = st.sidebar.text_input("Ruta Codebook",     codebook_default)
estructuras_path= st.sidebar.text_input("Ruta Estructuras",  estr_default)
hogares_path    = st.sidebar.text_input("Ruta Hogares",      hog_default)
limite_path     = st.sidebar.text_input("Ruta límites (GeoJSON)", lim_default)

apply_labels    = st.sidebar.checkbox("Aplicar etiquetas del codebook (si existen)", True)

dataset_choice  = st.sidebar.radio("Dataset a explorar", ["Unido (Estr↔Hog)", "Solo Estructuras", "Solo Hogares"], index=0)

st.sidebar.subheader("Mapa")
map_mode        = st.sidebar.selectbox("Modo", ["Puntos","Heatmap","Hexágonos","Grilla"], index=0)
color_dim_hint  = st.sidebar.text_input("Color por (categoría, opcional)", "SECTOR")
pt_size         = st.sidebar.slider("Tamaño de punto", 2, 80, 18, 1)
pt_opacity      = st.sidebar.slider("Opacidad de punto", 10, 255, 210, 5)
show_limits     = st.sidebar.checkbox("Mostrar límites", True)
fill_limits     = st.sidebar.checkbox("Rellenar límites", False)
limit_opacity   = st.sidebar.slider("Opacidad de límites", 10, 255, 60, 5)

# =============================
# Diagnóstico de archivos
# =============================
with st.expander("🔎 Diagnóstico de archivos", expanded=True):
    st.write({
        "Codebook existe": os.path.exists(codebook_path),
        "Estructuras existe": os.path.exists(estructuras_path),
        "Hogares existe": os.path.exists(hogares_path),
        "Límites (GeoJSON) existe": os.path.exists(limite_path),
        "pydeck instalado": _HAS_PYDECK,
    })

# =============================
# Carga
# =============================
with st.spinner("Leyendo archivos…"):
    def load_or_empty(p):
        try:
            df,_ = load_excel_first_sheet(p)
            return normalize_cols(df)
        except Exception as e:
            st.warning(f"Archivo no cargado ({p}): {e}")
            return pd.DataFrame()
    df_cb  = load_or_empty(codebook_path)
    df_estr= load_or_empty(estructuras_path)
    df_hog = load_or_empty(hogares_path)

meta = parse_codebook(df_cb)

# =============================
# Unión Estructuras ↔ Hogares
# =============================
st.sidebar.subheader("Unión Estructuras ↔ Hogares")
join_key="(no unir)"; join_how="left"; df_joined=df_estr.copy()
if not df_estr.empty and not df_hog.empty:
    cands = rank_join_candidates(df_estr.columns, df_hog.columns)
    cands_original = [pick_original_name(df_estr, c) for c in cands if pick_original_name(df_estr, c)]
    join_key = st.sidebar.selectbox("Clave de unión", ["(no unir)"] + cands_original)
    join_how = st.sidebar.selectbox("Tipo de unión", ["left","inner","right","outer"], index=0)
    if join_key != "(no unir)":
        key_hog = pick_original_name(df_hog, join_key.lower()) or (pick_original_name(df_hog, cands[0]) if cands else None)
        if key_hog:
            a=df_estr.copy(); b=df_hog.copy()
            a[join_key]=a[join_key].astype(str); b[key_hog]=b[key_hog].astype(str)
            df_joined = a.merge(b, left_on=join_key, right_on=key_hog, how=join_how, suffixes=("_estr","_hog"))
            st.sidebar.caption(f"Unidos por '{join_key}' ⇄ '{key_hog}' ({join_how}). Registros: {len(df_joined):,}")
        else:
            st.error("No se encontró la columna equivalente en Hogares.")

# Qué dataset mostramos
if dataset_choice=="Solo Estructuras": base_df=df_estr
elif dataset_choice=="Solo Hogares":   base_df=df_hog
else:                                  base_df=df_joined

df_display = apply_codebook(base_df, meta, apply_labels=apply_labels)

# =============================
# Pestañas
# =============================
tab1, tab2 = st.tabs(["📊 Análisis", "📖 Diccionario"])

with tab1:
    st.title("Dashboard del Territorio")
    st.caption("Filtra y explora estructuras y hogares; muestra límites de intervención.")

    # ---------- Filtros ----------
    st.sidebar.subheader("Filtros")
    if "clear_filters" not in st.session_state: st.session_state.clear_filters=False
    if st.sidebar.button("🧽 Limpiar filtros"): st.session_state.clear_filters=True

    if df_display.empty:
        st.info("No hay datos para filtrar. Revisa rutas/datasets en la barra lateral.")
        filtered=df_display
    else:
        cats = low_card_cats(df_display)
        defaults=[]
        lcols=[c.lower() for c in df_display.columns]
        for t in ["departamento","municipio","distrito","sector","es_hogar","hogar"]:
            if t in lcols: defaults.append(df_display.columns[lcols.index(t)])
        defaults=defaults[:5]

        selected = st.sidebar.multiselect("Columnas para filtrar (categóricas)", options=cats, default=([] if st.session_state.clear_filters else defaults))
        filtered = df_display.copy()
        for col in selected:
            vals = sorted([v for v in filtered[col].dropna().unique().tolist()], key=lambda x: str(x))
            picks_default = vals if not st.session_state.clear_filters else []
            picks = st.sidebar.multiselect(f"{col}", options=vals, default=picks_default)
            if picks: filtered = filtered[filtered[col].isin(picks)]

        if st.session_state.clear_filters: st.session_state.clear_filters=False

    # ---------- KPIs ----------
    c1,c2,c3,c4 = st.columns(4)
    with c1: st.metric("Registros (vista)", len(filtered))
    with c2: st.metric("Variables", filtered.shape[1] if not filtered.empty else 0)
    with c3:
        nn = float(filtered.notna().mean().mean()) if not filtered.empty else 0.0
        st.metric("% celdas no nulas (prom.)", f"{nn*100:.1f}%")
    with c4:
        hogar_cols=[c for c in filtered.columns if "hogar" in c.lower() or "es_hogar" in c.lower()]
        if hogar_cols and not filtered.empty:
            colh=hogar_cols[0]
            ser=filtered[colh].astype(str).str.strip().str.lower()
            rate=ser.isin(["1","si","sí","true","hogar","yes"]).mean()
            st.metric("% estructuras declaradas hogar", f"{rate*100:.1f}%")
        else:
            st.metric("% estructuras declaradas hogar", "—")

    st.divider()

    # ---------- Georreferencia ----------
    lat_guess, lon_guess = (guess_lat_lon(filtered) if not filtered.empty else (None,None))
    st.sidebar.subheader("Georreferencia")
    lat_col = st.sidebar.selectbox("Columna Latitud", ["(auto)"] + list(filtered.columns), index=(filtered.columns.get_loc(lat_guess)+1 if (not filtered.empty and lat_guess in filtered.columns) else 0))
    lon_col = st.sidebar.selectbox("Columna Longitud", ["(auto)"] + list(filtered.columns), index=(filtered.columns.get_loc(lon_guess)+1 if (not filtered.empty and lon_guess in filtered.columns) else 0))
    if lat_col=="(auto)": lat_col=lat_guess
    if lon_col=="(auto)": lon_col=lon_guess

    pts = pd.DataFrame()
    if lat_col and lon_col and not filtered.empty and lat_col in filtered.columns and lon_col in filtered.columns:
        pts = filtered[[lat_col, lon_col]].copy()
        pts[lat_col] = coerce_decimal(pts[lat_col])
        pts[lon_col] = coerce_decimal(pts[lon_col])
        pts = pts.rename(columns={lat_col:"lat", lon_col:"lon"}).dropna(subset=["lat","lon"])

    # ---------- Límites ----------
    gj=None
    if show_limits and os.path.exists(limite_path):
        try:
            with open(limite_path, "r", encoding="utf-8") as f:
                gj=json.load(f)
        except Exception as e:
            st.warning(f"No se pudo leer límites: {e}")

    # ---------- Centro del mapa ----------
    if not pts.empty:
        center_lat, center_lon = float(pts["lat"].median()), float(pts["lon"].median())
    elif gj is not None:
        center_lat, center_lon = geojson_center(gj)
    else:
        center_lat, center_lon = 13.494, -89.322

    # ---------- Colorear por categoría ----------
    color_dim = None
    if color_dim_hint and color_dim_hint in filtered.columns:
        color_dim = color_dim_hint
    else:
        # si existe una columna sector, úsala
        for cand in ["SECTOR","sector","Sector"]:
            if cand in filtered.columns: color_dim=cand; break

    color_df = None
    if color_dim and not pts.empty:
        color_df = filtered[[color_dim]].iloc[:len(pts)].copy().reset_index(drop=True)
        pts = pts.reset_index(drop=True)
        uniq = sorted(color_df[color_dim].dropna().astype(str).unique().tolist())
        pal = palette(len(uniq))
        colmap = {k: pal[i] for i, k in enumerate(uniq)}
        color_df["__color__"] = color_df[color_dim].astype(str).map(colmap)
        # Expand to RGBA columns
        rgba = color_df["__color__"].apply(lambda x: x+[int(pt_opacity)])
        pts["c_r"] = rgba.apply(lambda v: v[0]); pts["c_g"]=rgba.apply(lambda v: v[1])
        pts["c_b"] = rgba.apply(lambda v: v[2]); pts["c_a"]=rgba.apply(lambda v: v[3])

    # ---------- Render del mapa ----------
    if _HAS_PYDECK and (gj is not None or not pts.empty):
        layers=[]

        if gj is not None:
            layers.append(pdk.Layer(
                "GeoJsonLayer",
                data=gj,
                stroked=True,
                filled=bool(fill_limits),
                get_line_color=[0,0,0,255],
                get_line_width=2,
                get_fill_color=[255,255,255,int(limit_opacity)],
                pickable=True,
            ))

        if map_mode=="Puntos" and not pts.empty:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=pts,
                get_position="[lon, lat]",
                get_radius=int(pt_size),
                get_fill_color=("[c_r, c_g, c_b, c_a]" if color_df is not None else [0,128,255,int(pt_opacity)]),
                stroked=True,
                get_line_color=[0,0,0,200],
                line_width_min_pixels=0.5,
                pickable=False,
            ))
        elif map_mode=="Heatmap" and not pts.empty:
            layers.append(pdk.Layer(
                "HeatmapLayer",
                data=pts,
                get_position="[lon, lat]",
                aggregation='"SUM"',
            ))
        elif map_mode=="Hexágonos" and not pts.empty:
            layers.append(pdk.Layer(
                "HexagonLayer",
                data=pts,
                get_position="[lon, lat]",
                radius=40,
                elevation_scale=6,
                extruded=True,
                coverage=1,
                pickable=True,
            ))
        elif map_mode=="Grilla" and not pts.empty:
            layers.append(pdk.Layer(
                "GridLayer",
                data=pts,
                get_position="[lon, lat]",
                cell_size=60,
                extruded=False,
                pickable=True,
            ))

        st.subheader("Mapa")
        st.pydeck_chart(pdk.Deck(
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=12),
            layers=layers
        ))
    else:
        if not pts.empty:
            st.subheader("Mapa (simple)")
            st.map(pts, size=3, zoom=12)
        elif gj is not None and not _HAS_PYDECK:
            st.info("pydeck no está disponible: agrega 'pydeck>=0.8,<1' a requirements.txt para dibujar límites.")
        else:
            st.info("Sin puntos ni límites para mostrar. Revisa rutas, columnas Lat/Long o dataset.")

    # ---------- Tabla & descarga ----------
    st.subheader("Tabla filtrada")
    st.dataframe(filtered, use_container_width=True, height=420)
    st.download_button("⬇️ Descargar CSV filtrado",
        data=filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtrado.csv", mime="text/csv")

    st.divider()

    # ---------- Agregados + gráfico ----------
    if not filtered.empty:
        num_cols = [c for c in filtered.columns if pd.api.types.is_numeric_dtype(filtered[c])]
        dims = []
        # usar la misma dimensión de color si existe
        if color_dim and color_dim in filtered.columns:
            dims=[color_dim]
        elif low_card_cats(filtered):
            dims=[low_card_cats(filtered)[0]]

        if dims and num_cols:
            st.subheader("Exploración rápida (agregados)")
            grp = filtered.groupby(dims)[num_cols].agg(["count","mean","sum"]).reset_index()
            st.dataframe(grp, use_container_width=True, height=360)

            # Gráfico de barras simple (cuentas por la dimensión elegida)
            counts = filtered[dims[0]].astype(str).value_counts().sort_values(ascending=False).head(20)
            st.caption(f"Top categorías por {dims[0]} (conteo)")
            st.bar_chart(counts)
        else:
            st.caption("Sugerencia: asegúrate de tener al menos una columna categórica y alguna numérica para ver agregados.")

with tab2:
    st.title("Diccionario (Codebook)")
    if df_cb is None or df_cb.empty:
        st.info("No se pudo mostrar el codebook (vacío o no cargado).")
    else:
        st.dataframe(df_cb, use_container_width=True, height=620)
        st.caption("Activa 'Aplicar etiquetas del codebook' en la barra lateral para ver categorías decodificadas.")
