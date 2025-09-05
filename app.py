
import os, json, glob
import streamlit as st
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

# Try to import pydeck for polygon rendering
try:
    import pydeck as pdk
    _HAS_PYDECK = True
except Exception:
    _HAS_PYDECK = False

st.set_page_config(page_title="Dashboard Territorio: Estructuras y Hogares", layout="wide")

# ============================
# Helpers
# ============================
def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def to_lower_set(cols) -> set:
    return set([str(c).strip().lower() for c in cols])

def find_firstExisting(candidates: List[str]) -> Optional[str]:
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

def auto_glob(patterns: List[str]) -> Optional[str]:
    for pat in patterns:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    return None

def load_excel_first_sheet(path: str) -> Tuple[pd.DataFrame, List[str]]:
    xls = pd.ExcelFile(path)
    first = xls.sheet_names[0]
    df = pd.read_excel(path, sheet_name=first)
    return df, xls.sheet_names

def low_cardinality_categoricals(df: pd.DataFrame, max_unique: int = 60) -> List[str]:
    out = []
    for c in df.columns:
        series = df[c]
        nun = series.nunique(dropna=True)
        if nun <= max_unique:
            if series.dtype == "object" or pd.api.types.is_bool_dtype(series) or nun <= 20:
                out.append(c)
    return out

def guess_lat_lon(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    lat_candidates = ["lat", "latitude", "y", "p002__latitude", "latitud", "coord_y", "y_wgs84"]
    lon_candidates = ["lon", "lng", "longitude", "x", "p002__longitude", "longitud", "coord_x", "x_wgs84"]
    cols_lower = {str(c).lower(): c for c in df.columns}
    lat = next((cols_lower[c] for c in lat_candidates if c in cols_lower), None)
    lon = next((cols_lower[c] for c in lon_candidates if c in cols_lower), None)
    return lat, lon

def coerce_datetime(s: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(s, errors="ignore", infer_datetime_format=True)
    except Exception:
        return s

def parse_codebook(df_cb: pd.DataFrame) -> Dict[str, Dict]:
    meta: Dict[str, Dict] = {}
    if df_cb is None or df_cb.empty:
        return meta

    cb = df_cb.copy()
    cb.columns = [str(c).strip() for c in cb.columns]
    lc = {c.lower(): c for c in cb.columns}

    var_col = next((lc[k] for k in ["variable", "var", "nombre", "campo", "name"] if k in lc), None)
    type_col = next((lc[k] for k in ["tipo", "type", "data_type", "clase", "class"] if k in lc), None)
    value_col = next((lc[k] for k in ["valor", "value", "code", "código", "codigo", "option_value"] if k in lc), None)
    label_col = next((lc[k] for k in ["etiqueta", "label", "meaning", "categoria", "categoría", "option_label"] if k in lc), None)
    opciones_col = next((lc[k] for k in ["opciones", "categorias", "categorías", "levels", "choices"] if k in lc), None)

    if var_col and (value_col and label_col):
        for var, g in cb.groupby(var_col, dropna=True):
            var = str(var).strip()
            mapping = {}
            for _, row in g.iterrows():
                code = row.get(value_col)
                lab = row.get(label_col)
                if pd.isna(code) or str(code).strip() == "":
                    continue
                mapping[str(code)] = str(lab) if not pd.isna(lab) else str(code)
            vtype = None
            if type_col and not g[type_col].isna().all():
                vtype = str(g[type_col].dropna().iloc[0]).strip().lower()
            meta[var] = {"type": vtype, "label": None, "map": mapping, "raw_type": vtype}

    if var_col and opciones_col:
        for _, row in cb.iterrows():
            var = row.get(var_col)
            var = None if pd.isna(var) else str(var).strip()
            if not var:
                continue
            opts = row.get(opciones_col)
            if pd.isna(opts):
                continue
            mapping = {}
            for piece in str(opts).replace(",", ";").split(";"):
                piece = piece.strip()
                if not piece:
                    continue
                if "=" in piece:
                    k, v = piece.split("=", 1)
                elif ":" in piece:
                    k, v = piece.split(":", 1)
                else:
                    continue
                mapping[str(k).strip()] = str(v).strip()
            vtype = None
            if type_col and not pd.isna(row.get(type_col)):
                vtype = str(row.get(type_col)).strip().lower()
            if var not in meta:
                meta[var] = {"type": vtype, "label": None, "map": mapping, "raw_type": vtype}
            else:
                meta[var]["map"] = {**meta[var]["map"], **mapping}

    if var_col and type_col:
        for _, row in cb.iterrows():
            var = row.get(var_col)
            var = None if pd.isna(var) else str(var).strip()
            if not var:
                continue
            vtype = row.get(type_col)
            if pd.isna(vtype):
                continue
            vtype = str(vtype).strip().lower()
            meta.setdefault(var, {"type": None, "label": None, "map": {}, "raw_type": None})
            meta[var]["type"] = meta[var]["type"] or vtype
            meta[var]["raw_type"] = meta[var]["raw_type"] or vtype

    return meta

def apply_codebook_types_and_labels(df: pd.DataFrame, meta: Dict[str, Dict], apply_labels: bool) -> pd.DataFrame:
    if df is None or df.empty or not meta:
        return df

    out = df.copy()
    cols_lower = {c.lower(): c for c in out.columns}

    for var_name, info in meta.items():
        col = cols_lower.get(var_name.lower())
        if not col:
            continue

        vtype = (info or {}).get("type")
        if vtype:
            if any(k in vtype for k in ["num", "int", "float", "double", "decimal"]):
                out[col] = pd.to_numeric(out[col], errors="ignore")
            elif any(k in vtype for k in ["date", "fecha", "time"]):
                out[col] = coerce_datetime(out[col])

        mapping = (info or {}).get("map", {}) or {}
        if apply_labels and mapping:
            raw_col = f"{col}_raw"
            if raw_col not in out.columns:
                out[raw_col] = out[col]
            out[col] = out[col].apply(lambda x: mapping.get(str(x), x))

    return out

def rank_join_candidates(cols_a: List[str], cols_b: List[str]) -> List[str]:
    la = to_lower_set(cols_a); lb = to_lower_set(cols_b)
    inter = list(la.intersection(lb))
    def score(name: str) -> int:
        s = 0
        if "id" in name: s += 3
        if "estructura" in name or "struct" in name: s += 2
        if "codigo" in name or "código" in name or "code" in name: s += 1
        if name.endswith("_id") or name.startswith("id_"): s += 2
        return s
    return sorted(inter, key=lambda n: (-score(n), n))

def pick_original_name(df: pd.DataFrame, lower_name: str) -> Optional[str]:
    for c in df.columns:
        if str(c).strip().lower() == lower_name:
            return c
    return None

def fix_decimal_commas(series: pd.Series) -> pd.Series:
    """Replace comma decimal separators with dot and convert to numeric."""
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")

# ============================
# Sidebar: Inputs
# ============================
st.sidebar.header("Datos de entrada")

# Try to auto-find files if the default doesn't exist
codebook_default = find_firstExisting([
    "data/metadata/Codebook.xlsx",
    auto_glob("**/Codebook.xlsx")
]) or "data/metadata/Codebook.xlsx"

estructuras_default = find_firstExisting([
    "data/private/basedarboard.xlsx",
    auto_glob("**/basedarboard.xlsx")
]) or "data/private/basedarboard.xlsx"

hogares_default = find_firstExisting([
    "data/private/hogares.xlsx",
    auto_glob("**/hogares.xlsx")
]) or "data/private/hogares.xlsx"

limite_default = find_firstExisting([
    "data/gis/areas_intervencion.geojson",
    auto_glob("**/areas_intervencion.geojson"),
    auto_glob("**/*intervencion*.geojson"),
    auto_glob("**/*limite*.geojson")
]) or "data/gis/areas_intervencion.geojson"

codebook_path = st.sidebar.text_input("Ruta Codebook", codebook_default)
estructuras_path = st.sidebar.text_input("Ruta Estructuras", estructuras_default)
hogares_path = st.sidebar.text_input("Ruta Hogares", hogares_default)
limite_path = st.sidebar.text_input("Ruta límites (GeoJSON)", limite_default)

apply_labels = st.sidebar.checkbox("Aplicar etiquetas del codebook (si existen)", value=True)
mostrar_limites = st.sidebar.checkbox("Mostrar límites de intervención", value=True)
relleno_limites = st.sidebar.checkbox("Rellenar polígonos", value=False)

# ============================
# Load data with clear diagnostics
# ============================
with st.expander("🔎 Diagnóstico de archivos", expanded=True):
    st.write({
        "Codebook existe": os.path.exists(codebook_path),
        "Estructuras existe": os.path.exists(estructuras_path),
        "Hogares existe": os.path.exists(hogares_path),
        "Límites (GeoJSON) existe": os.path.exists(limite_path),
        "pydeck instalado": _HAS_PYDECK,
    })

with st.spinner("Leyendo archivos..."):
    errs = []
    try:
        df_cb, _ = load_excel_first_sheet(codebook_path)
    except Exception as e:
        errs.append(f"Codebook: {e}")
        df_cb = pd.DataFrame()

    try:
        df_estr, _ = load_excel_first_sheet(estructuras_path)
    except Exception as e:
        errs.append(f"Estructuras: {e}")
        df_estr = pd.DataFrame()

    try:
        df_hog, _ = load_excel_first_sheet(hogares_path)
    except Exception as e:
        errs.append(f"Hogares: {e}")
        df_hog = pd.DataFrame()

if errs:
    st.warning("Problemas al leer archivos:\n- " + "\n- ".join(errs))

df_cb = normalize_cols(df_cb) if not df_cb.empty else df_cb
df_estr = normalize_cols(df_estr) if not df_estr.empty else df_estr
df_hog = normalize_cols(df_hog) if not df_hog.empty else df_hog

# Attempt to fix decimal commas in obvious lat/lon columns
for df in [df_estr, df_hog]:
    if not df.empty:
        lat_c, lon_c = guess_lat_lon(df)
        if lat_c and lon_c:
            try:
                df[lat_c] = fix_decimal_commas(df[lat_c])
                df[lon_c] = fix_decimal_commas(df[lon_c])
            except Exception:
                pass

# ============================
# Codebook
# ============================
meta = parse_codebook(df_cb)

# ============================
# Join
# ============================
st.sidebar.subheader("Unión Estructuras ↔ Hogares")
if df_estr.empty:
    st.warning("No hay datos de Estructuras cargados.")
if df_hog.empty:
    st.warning("No hay datos de Hogares cargados.")

join_key = "(no unir)"
join_how = "left"
df_joined = df_estr.copy()

if not df_estr.empty and not df_hog.empty:
    cands = rank_join_candidates(list(df_estr.columns), list(df_hog.columns))
    cands_original = [pick_original_name(df_estr, c) for c in cands if pick_original_name(df_estr, c)]
    join_key = st.sidebar.selectbox("Clave de unión (auto-detección)", options=(["(no unir)"] + cands_original))
    join_how = st.sidebar.selectbox("Tipo de unión", options=["left", "inner", "right", "outer"], index=0)

    if join_key != "(no unir)":
        key_hog = pick_original_name(df_hog, join_key.lower())
        if key_hog is None and cands:
            key_hog = pick_original_name(df_hog, cands[0])
        if key_hog is None:
            st.error("No se encontró la columna equivalente en Hogares.")
        else:
            a = df_estr.copy(); b = df_hog.copy()
            a[join_key] = a[join_key].astype(str)
            b[key_hog] = b[key_hog].astype(str)
            df_joined = a.merge(b, left_on=join_key, right_on=key_hog, how=join_how, suffixes=("_estr", "_hog"))
            st.sidebar.caption(f"Unidos por '{join_key}' ⇄ '{key_hog}' ({join_how}). Registros resultantes: {len(df_joined):,}")

df_display = apply_codebook_types_and_labels(df_joined, meta, apply_labels=apply_labels)

# ============================
# Tabs
# ============================
tab1, tab2 = st.tabs(["📊 Análisis", "📖 Diccionario"])

with tab1:
    st.title("Dashboard del Territorio")
    st.caption("Filtra y explora estructuras y hogares; muestra límites de intervención.")

    # --------- Filters ---------
    st.sidebar.subheader("Filtros")
    if df_display.empty:
        st.info("No hay datos para filtrar (revisa el diagnóstico de archivos y las rutas).")
        filtered = df_display
    else:
        candidatas = low_cardinality_categoricals(df_display)
        defaults = []
        lcols = [c.lower() for c in df_display.columns]
        for target in ["departamento", "municipio", "distrito", "sector", "es_hogar", "hogar"]:
            if target in lcols:
                defaults.append(df_display.columns[lcols.index(target)])
        defaults = defaults[:5]

        selected = st.sidebar.multiselect("Columnas para filtrar (categóricas)", options=candidatas, default=defaults)

        filtered = df_display.copy()
        for col in selected:
            vals = sorted([v for v in filtered[col].dropna().unique().tolist()], key=lambda x: str(x))
            picks = st.sidebar.multiselect(f"{col}", options=vals, default=vals)
            if picks:
                filtered = filtered[filtered[col].isin(picks)]

    # --------- KPIs ---------
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("Registros (vista)", len(filtered))
    with c2: st.metric("Variables", filtered.shape[1] if not filtered.empty else 0)
    with c3:
        nn = float(filtered.notna().mean().mean()) if not filtered.empty else 0.0
        st.metric("% celdas no nulas (prom.)", f"{nn*100:.1f}%")
    with c4:
        hogar_cols = [c for c in filtered.columns if "hogar" in c.lower() or "es_hogar" in c.lower()]
        if hogar_cols and not filtered.empty:
            colh = hogar_cols[0]
            ser = filtered[colh].astype(str).str.strip().str.lower()
            rate = ser.isin(["1", "si", "sí", "true", "hogar"]).mean()
            st.metric("% estructuras declaradas hogar", f"{rate*100:.1f}%")
        else:
            st.metric("% estructuras declaradas hogar", "—")

    st.divider()

    # --------- Map ---------
    lat_col, lon_col = guess_lat_lon(filtered)

    # Load GeoJSON if exists
    gj = None
    if mostrar_limites and os.path.exists(limite_path):
        try:
            with open(limite_path, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception as e:
            st.warning(f"No se pudo leer el GeoJSON de límites: {e}")

    # Prepare points
    pts = pd.DataFrame()
    if lat_col and lon_col and not filtered.empty:
        pts = filtered[[lat_col, lon_col]].dropna().rename(columns={lat_col:"lat", lon_col:"lon"})
        pts["lat"] = pd.to_numeric(pts["lat"], errors="coerce")
        pts["lon"] = pd.to_numeric(pts["lon"], errors="coerce")
        pts = pts.dropna()

    # Choose map center
    if not pts.empty:
        center_lat, center_lon = float(pts["lat"].median()), float(pts["lon"].median())
    else:
        # If no points, try to center over a known location (Puerto de La Libertad) or leave default
        center_lat, center_lon = 13.494, -89.322

    if _HAS_PYDECK and (gj is not None or not pts.empty):
        layers = []
        if gj is not None:
            layers.append(pdk.Layer(
                "GeoJsonLayer",
                data=gj,
                stroked=True,
                filled=bool(relleno_limites),
                get_line_color=[0, 0, 0, 220],
                get_line_width=2,
                get_fill_color=[255, 255, 255, 30],
                pickable=True,
            ))
        if not pts.empty:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=pts,
                get_position="[lon, lat]",
                get_radius=25,
                get_fill_color=[0, 128, 255, 160],
                pickable=False,
            ))
        st.subheader("Mapa")
        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=11),
            layers=layers
        ))
    else:
        # Fallback simple map
        if not pts.empty:
            st.subheader("Mapa")
            st.map(pts, size=3, zoom=11)
        elif gj is not None:
            st.info("pydeck no está disponible, por lo que no se puede dibujar el GeoJSON. Añade 'pydeck>=0.8,<1' en requirements.txt.")
        else:
            st.info("No hay puntos ni límites para mostrar. Revisa las rutas y las columnas de lat/lon.")

    st.subheader("Tabla filtrada")
    st.dataframe(filtered, use_container_width=True, height=420)

    st.download_button(
        "⬇️ Descargar CSV filtrado",
        data=filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtrado.csv",
        mime="text/csv"
    )

    st.divider()

    # --------- Quick Aggr ---------
    if not filtered.empty:
        num_cols = [c for c in filtered.columns if pd.api.types.is_numeric_dtype(filtered[c])]
        if num_cols:
            dims = selected if selected else []
            if dims:
                st.subheader("Exploración rápida (agregados)")
                try:
                    grp = filtered.groupby(dims)[num_cols].agg(["count", "mean", "sum"]).reset_index()
                    st.dataframe(grp, use_container_width=True, height=360)
                except Exception as e:
                    st.info(f"No fue posible calcular agregados con los filtros actuales: {e}")
            else:
                st.caption("Sugerencia: elige 1–3 columnas categóricas en 'Columnas para filtrar' para ver agregados por grupo.")

with tab2:
    st.title("Diccionario (Codebook)")
    if df_cb is None or df_cb.empty:
        st.info("No se pudo mostrar el codebook (vacío o no cargado).")
    else:
        st.dataframe(df_cb, use_container_width=True, height=600)
        st.caption("Activa 'Aplicar etiquetas del codebook' en la barra lateral para ver categorías decodificadas cuando existan.")
