# app.py — Dashboard Territorio con Anexo Estadístico por Sector/Bloque
# Requisitos: streamlit, pandas, numpy, openpyxl, (opcional) pydeck
import os, json, glob, math, re
import streamlit as st
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

st.set_page_config(page_title="Dashboard Territorio", layout="wide")

# ====== pydeck opcional (mapa y polígonos) ======
try:
    import pydeck as pdk
    _HAS_PYDECK = True
except Exception:
    _HAS_PYDECK = False

# ====== Estilo ======
st.markdown("""
<style>
.block-container {padding-top: .9rem; padding-bottom: 2rem; max-width: 1400px;}
.stMetric {background: rgba(255,255,255,0.035); border-radius: 12px; padding: .6rem .9rem;}
[data-testid="stSidebar"] {min-width: 340px;}
</style>
""", unsafe_allow_html=True)

# ====== Utilidades ======
def to_lower(x): 
    try: return str(x).strip().lower()
    except: return x

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    df = df.copy(); df.columns = [str(c).strip() for c in df.columns]; return df

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

# ====== Codebook (parser robusto + renombrado) ======
ef _find_col(df, *aliases):
    cols_lc = {str(c).strip().lower(): c for c in df.columns}
    # match exacto primero
    for a in aliases:
        a_lc = str(a).strip().lower()
        if a_lc in cols_lc: 
            return cols_lc[a_lc]
    # match por "contiene"
    for a in aliases:
        a_lc = str(a).strip().lower()
        for k,orig in cols_lc.items():
            if a_lc in k: 
                return orig
    return None

def parse_codebook_any(path: str) -> Tuple[pd.DataFrame, Dict[str, Dict], Dict[str, pd.DataFrame], Dict[str, str]]:
    """
    Devuelve:
      - df_vars: DataFrame con columnas ['variable','tipo','descripcion','nuevo_nombre']
      - meta_lc: {variable_lower: {'type': str|None, 'map': {raw->label}}}
      - maps_por_var: {variable_lower: DataFrame(codigo, etiqueta)}
      - ren_lc: {variable_lower_original: nuevo_nombre_visible}  (para renombrar columnas)
    """
    xls = pd.ExcelFile(path)
    dfs = {s: normalize_cols(pd.read_excel(path, sheet_name=s)) for s in xls.sheet_names}

    df_vars = pd.DataFrame(columns=["variable","tipo","descripcion","nuevo_nombre"])
    meta: Dict[str, Dict] = {}
    maps_por_var: Dict[str, pd.DataFrame] = {}
    ren_map_raw: Dict[str, str] = {}

    for s, df in dfs.items():
        if df is None or df.empty:
            continue

        # --- Detecta columnas con nombres “similares” a los de tu codebook
        var_col  = _find_col(df, "variable","var","nombre","campo","name")
        tipo_col = _find_col(df, "tipo","type","data_type","clase","class","tipo de variable")
        desc_col = _find_col(df, "descripcion","descripción","description","detalle","definicion","definición","etiqueta de variable (desc)")
        newn_col = _find_col(df, "nuevo_nombre","new_name","etiqueta_variable","etiqueta de variable",
                                  "label_variable","display_name","nombre_publico","nombre público","nombre mostrado")
        code_col = _find_col(df, "valor","value","code","código","codigo","option_value","código")
        lab_col  = _find_col(df, "etiqueta","label","meaning","categoria","categoría",
                                  "option_label","etiqueta del código","etiqueta del codigo")

        # --- Variables (nombre visible y tipo)
        if var_col:
            tmp = df[df[var_col].notna()][[var_col] + ([tipo_col] if tipo_col else []) + ([newn_col] if newn_col else [])].copy()
            # Renombra columnas normalizadas
            rename_map = {var_col: "variable"}
            if tipo_col: rename_map[tipo_col] = "tipo"
            if newn_col: rename_map[newn_col] = "nuevo_nombre"
            tmp = tmp.rename(columns=rename_map)
            tmp["variable"] = tmp["variable"].astype(str).str.strip()
            if "tipo" in tmp.columns:
                tmp["tipo"] = tmp["tipo"].astype(str).str.strip()
            if "nuevo_nombre" in tmp.columns:
                tmp["nuevo_nombre"] = tmp["nuevo_nombre"].astype(str).str.strip()
            df_vars = (pd.concat([df_vars, tmp], ignore_index=True)
                         .drop_duplicates(subset=["variable"], keep="first"))

        # --- Renombrado visible
        if newn_col and var_col:
            for _, r in df.dropna(subset=[var_col, newn_col]).iterrows():
                v = str(r[var_col]).strip()
                nn = str(r[newn_col]).strip()
                if v and nn:
                    ren_map_raw[v] = nn

        # --- Mapeos de valores (formato largo con ffill)
        if var_col and code_col and lab_col:
            t = df[[var_col, code_col, lab_col]].copy()
            t[var_col] = t[var_col].ffill()
            t = t.dropna(subset=[code_col, lab_col])
            for _, r in t.iterrows():
                v = str(r[var_col]).strip()
                k = str(r[code_col]).strip().rstrip(".0")
                lbl = None if pd.isna(r[lab_col]) else str(r[lab_col]).strip()
                meta.setdefault(v, {"type": None, "map": {}})
                meta[v]["map"][k] = lbl

        # --- Tipos desde la hoja si no estaban
        if var_col and tipo_col:
            for _, r in df.dropna(subset=[var_col, tipo_col]).iterrows():
                v = str(r[var_col]).strip()
                vt = str(r[tipo_col]).strip().lower()
                meta.setdefault(v, {"type": None, "map": {}})
                if meta[v]["type"] is None:
                    meta[v]["type"] = vt

    # Completar df_vars con tipos del meta si faltan
    if not df_vars.empty:
        df_vars = df_vars.drop_duplicates(subset=["variable"])
        df_vars["tipo"] = df_vars.apply(lambda r: (meta.get(str(r["variable"]), {}).get("type") or r.get("tipo")), axis=1)
    else:
        # si no hubo filas “variables”, construimos desde meta
        df_vars = pd.DataFrame([{
            "variable": v, "tipo": meta.get(v,{}).get("type"), "descripcion": None, "nuevo_nombre": ren_map_raw.get(v)
        } for v in meta.keys()])

    # Tablas de mapeo por variable (útil para Tab 2)
    for v, info in meta.items():
        mp = info.get("map", {}) or {}
        if mp:
            df_map = pd.DataFrame({"codigo": list(mp.keys()), "etiqueta": [mp[k] for k in mp.keys()]})
            maps_por_var[v] = df_map.sort_values("codigo", key=lambda s: s.astype(str))

    # Normaliza a lowercase para acceso robusto
    meta_lc = {to_lower(k): {"type": v.get("type"), "map": v.get("map", {})} for k, v in meta.items()}
    ren_lc  = {to_lower(k): ren_map_raw[k] for k in ren_map_raw.keys()}
    maps_lc = {to_lower(k): val for k, val in maps_por_var.items()}

    return df_vars, meta_lc, maps_lc, ren_lc


    def _ingesta_vars(df):
        nonlocal df_vars, ren_map_raw
        if df is None or df.empty: return
        var_col  = _find_col(df, "variable","var","nombre","campo","name")
        tipo_col = _find_col(df, "tipo","type","data_type","clase","class")
        desc_col = _find_col(df, "descripcion","descripción","description","label","detalle","definicion","definición")
        newn_col = _find_col(df, "nuevo_nombre","new_name","etiqueta_variable","label_variable","nombre_publico","nombre_mostrado","display_name")

        if var_col:
            tmp = pd.DataFrame({
                "variable": df[var_col].astype(str).str.strip(),
                "tipo": df[tipo_col].astype(str).str.strip() if tipo_col else None,
                "descripcion": df[desc_col].astype(str).str.strip() if desc_col else None,
                "nuevo_nombre": df[newn_col].astype(str).str.strip() if newn_col else None
            })
            tmp = tmp.dropna(subset=["variable"])
            # agrega a df_vars (sin duplicar variables)
            df_vars = (pd.concat([df_vars, tmp], ignore_index=True)
                        .drop_duplicates(subset=["variable"], keep="first"))
            # renombres
            if newn_col:
                for _, r in df.dropna(subset=[var_col, newn_col]).iterrows():
                    v = str(r[var_col]).strip()
                    nn = str(r[newn_col]).strip()
                    if v and nn:
                        ren_map_raw[v] = nn

    def _agrega_map(var, code, label):
        if var is None or str(var).strip()=="":
            return
        v = str(var).strip()
        meta.setdefault(v, {"type": None, "map": {}})
        if code is not None and str(code).strip()!="":
            meta[v]["map"][str(code)] = (None if label is None else str(label))

    # Detecta formatos largos/ancho/pareados en TODAS las hojas
    for s, df in dfs.items():
        if df is None or df.empty: continue
        _ingesta_vars(df)

        var_col = _find_col(df,"variable","var","nombre","campo","name")
        code_col = _find_col(df,"valor","value","code","código","codigo","option_value")
        lab_col  = _find_col(df,"etiqueta","label","meaning","categoria","categoría","option_label")
        tipo_col = _find_col(df,"tipo","type","data_type","clase","class")
        opc_col  = _find_col(df,"opciones","categorias","categorías","levels","choices")

        # Largo clásico
        if var_col and code_col and lab_col:
            for _,r in df.iterrows():
                _agrega_map(r.get(var_col), r.get(code_col), r.get(lab_col))
                if tipo_col and not pd.isna(r.get(tipo_col)):
                    v = str(r.get(var_col)).strip()
                    vt = str(r.get(tipo_col)).strip().lower()
                    meta.setdefault(v, {"type": None, "map": {}})
                    if meta[v]["type"] is None: meta[v]["type"]=vt

        # Ancho: "1=Si;2=No"
        if var_col and opc_col and df[opc_col].notna().any():
            for _,r in df.iterrows():
                v = r.get(var_col); opts = r.get(opc_col)
                if pd.isna(v) or pd.isna(opts): continue
                for piece in str(opts).replace(",", ";").split(";"):
                    piece=piece.strip()
                    if not piece: continue
                    if "=" in piece: k,vv = piece.split("=",1)
                    elif ":" in piece: k,vv = piece.split(":",1)
                    else: continue
                    _agrega_map(v, str(k).strip(), str(vv).strip())
                if tipo_col and not pd.isna(r.get(tipo_col)):
                    vt = str(r.get(tipo_col)).strip().lower()
                    vv = str(v).strip()
                    meta.setdefault(vv, {"type": None, "map": {}})
                    if meta[vv]["type"] is None: meta[vv]["type"]=vt

        # Pareados Valor1/Etiqueta1
        valor_cols = [c for c in df.columns if re.search(r"valor\s*\d+", to_lower(c))]
        if valor_cols:
            for c in df.columns:
                m = re.search(r"valor\s*(\d+)", to_lower(c))
                if not m: continue
                i = m.group(1)
                lab_cands = [x for x in df.columns if re.search(fr"etiqueta\s*{i}", to_lower(x))]
                if not lab_cands: continue
                labc = lab_cands[0]
                varc = var_col
                if not varc: continue
                for _,r in df.iterrows():
                    _agrega_map(r.get(varc), r.get(c), r.get(labc))

    # Completar df_vars con tipos del meta si faltan
    if not df_vars.empty:
        df_vars = df_vars.drop_duplicates(subset=["variable"])
        df_vars["tipo"] = df_vars.apply(
            lambda r: (meta.get(str(r["variable"]), {}).get("type") or r.get("tipo")), axis=1
        )
    else:
        df_vars = pd.DataFrame(
            [{"variable": v, "tipo": meta.get(v,{}).get("type"), "descripcion": None, "nuevo_nombre": None} for v in meta.keys()]
        )

    # Tablas de mapeo por variable
    maps_por_var={}
    for v, info in meta.items():
        mp = info.get("map", {}) or {}
        if mp:
            df_map = pd.DataFrame({"codigo": list(mp.keys()), "etiqueta": [mp[k] for k in mp.keys()]})
            maps_por_var[v] = df_map.sort_values("codigo", key=lambda s: s.astype(str))

    # Keys a lowercase para acceso flexible
    meta_lc = {to_lower(k): {"type": v.get("type"), "map": v.get("map", {})} for k,v in meta.items()}
    maps_lc = {to_lower(k): val for k,val in maps_por_var.items()}
    # Mapa de renombrado a lowercase
    ren_lc = {to_lower(k): ren_map_raw[k] for k in ren_map_raw.keys()}

    return df_vars, meta_lc, maps_lc, ren_lc

def apply_codebook(df: pd.DataFrame, df_vars: pd.DataFrame, meta_lc: Dict[str, Dict], ren_lc: Dict[str, str], apply_labels: bool=True) -> pd.DataFrame:
    """
    - Renombra columnas según ren_lc si existe nombre público/nuevo.
    - Tipifica por 'tipo' (numérico/fecha).
    - Aplica mapeos de etiquetas a valores.
    """
    if df is None or df.empty: return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    # 1) Renombrar columnas según codebook (evita colisiones)
    cols_lower = {to_lower(c): c for c in out.columns}
    new_names = {}
    taken = set(out.columns)
    for v_lower, col in cols_lower.items():
        if v_lower in ren_lc:
            cand = str(ren_lc[v_lower]).strip()
            if cand and cand not in taken:
                new_names[col] = cand
                taken.add(cand)
            # si colisiona, deja nombre original para no romper
    if new_names:
        out = out.rename(columns=new_names)
        # reconstruye cols_lower luego del rename
        cols_lower = {to_lower(c): c for c in out.columns}

    # 2) Tipificar por 'tipo'
    tipos = {}
    if df_vars is not None and not df_vars.empty:
        for _, r in df_vars.iterrows():
            v = str(r.get("variable","")).strip()
            if not v: continue
            t = r.get("tipo")
            tipos[to_lower(v)] = (None if pd.isna(t) else str(t).strip().lower())

    for v_lower, col in cols_lower.items():
        vtype = tipos.get(v_lower) or (meta_lc.get(v_lower, {}) or {}).get("type")
        if vtype:
            if any(k in vtype for k in ["num","int","float","double","decimal"]):
                out[col] = pd.to_numeric(out[col], errors="ignore")
            elif any(k in vtype for k in ["date","fecha","time"]):
                try: out[col] = pd.to_datetime(out[col], errors="ignore", infer_datetime_format=True)
                except Exception: pass

    # 3) Aplicar mapeos de etiquetas
    if apply_labels and meta_lc:
        # re-calcula cols_lower por si cambió algo
        cols_lower = {to_lower(c): c for c in out.columns}
        for v_lower, col in cols_lower.items():
            info = meta_lc.get(v_lower)
            if not info: continue
            mapping = info.get("map", {}) or {}
            if mapping:
                raw_col = f"{col}_raw"
                if raw_col not in out.columns: out[raw_col] = out[col]
                out[col] = out[col].apply(lambda x: mapping.get(str(x), mapping.get(x, x)))
    return out

def rank_join_candidates(cols_a, cols_b):
    la, lb = {to_lower(c) for c in cols_a}, {to_lower(c) for c in cols_b}
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
        if to_lower(c)==lower_name: return c
    return None

# ====== Sidebar: rutas ======
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

# ====== Diagnóstico ======
with st.expander("🔎 Diagnóstico de archivos", expanded=True):
    st.write({
        "Codebook existe": os.path.exists(codebook_path),
        "Estructuras existe": os.path.exists(estructuras_path),
        "Hogares existe": os.path.exists(hogares_path),
        "Límites (GeoJSON) existe": os.path.exists(limite_path),
        "pydeck instalado": _HAS_PYDECK,
    })

# ====== Carga ======
with st.spinner("Leyendo archivos…"):
    def load_or_empty(p):
        try:
            df,_ = load_excel_first_sheet(p)
            return normalize_cols(df)
        except Exception as e:
            st.warning(f"Archivo no cargado ({p}): {e}")
            return pd.DataFrame()
    # Codebook robusto
    try:
        df_vars, meta_lc, maps_por_var, ren_lc = parse_codebook_any(codebook_path)
    except Exception as e:
        st.warning(f"No se pudo parsear el codebook: {e}.")
        df_vars, meta_lc, maps_por_var, ren_lc = pd.DataFrame(), {}, {}, {}

    df_estr = load_or_empty(estructuras_path)
    df_hog  = load_or_empty(hogares_path)

# ====== Unión Estructuras ↔ Hogares ======
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

# Dataset base
if dataset_choice=="Solo Estructuras": base_df=df_estr
elif dataset_choice=="Solo Hogares":   base_df=df_hog
else:                                  base_df=df_joined

# Aplicar codebook (incluye RENOMBRADO si hay 'nuevo_nombre' en el codebook)
df_display = apply_codebook(base_df, df_vars, meta_lc, ren_lc, apply_labels=apply_labels)

# ====== Filtros comunes ======
def sector_column(df):
    for c in df.columns:
        cl = str(c).strip().lower()
        if "sector" in cl or "bloque" in cl:
            return c
    return None


sector_col = sector_column(df_display)

# ====== Tabs ======
tab1, tab2, tab3 = st.tabs(["📊 Análisis", "📖 Diccionario", "📑 Anexo Estadístico (por Sector/Bloque)"])

# ------------------------- TAB 1: Análisis general -------------------------
with tab1:
    st.title("Dashboard del Territorio")
    st.caption("Filtros, KPIs y mapa con límites de intervención.")

    # ---------- Filtros (con hotfix de defaults válidos) ----------
    st.sidebar.subheader("Filtros")
    cats = low_card_cats(df_display)

    # candidatos por defecto (pueden NO ser de baja cardinalidad)
    defaults=[]
    lcols=[c.lower() for c in df_display.columns]
    for t in ["departamento","municipio","distrito","sector","es_hogar","hogar"]:
        if t in lcols: defaults.append(df_display.columns[lcols.index(t)])
    defaults=defaults[:5]

    # filtro específico de sector destacado
    if sector_col:
        opt_sector = sorted(df_display[sector_col].dropna().astype(str).unique().tolist())
        pick_sector = st.sidebar.multiselect("Filtrar por Sector/Bloque", opt_sector, default=opt_sector)
    else:
        pick_sector = None

    options_cats = [c for c in cats if c!=sector_col]
    valid_defaults = [d for d in defaults if d in options_cats]

    if options_cats:
        other_selected = st.sidebar.multiselect(
            "Otras columnas para filtrar (categóricas)",
            options=options_cats,
            default=valid_defaults
        )
    else:
        st.sidebar.caption("No se detectaron columnas categóricas de baja cardinalidad.")
        other_selected = []

    # Aplica filtros
    filtered = df_display.copy()
    if sector_col and pick_sector:
        filtered = filtered[filtered[sector_col].astype(str).isin(pick_sector)]
    for col in other_selected:
        vals = sorted([v for v in filtered[col].dropna().unique().tolist()], key=lambda x: str(x))
        picks = st.sidebar.multiselect(f"{col}", options=vals, default=vals)
        if picks: filtered = filtered[filtered[col].isin(picks)]

    # KPIs
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

    # Georreferencia + mapa
    lat_guess, lon_guess = (guess_lat_lon(filtered) if not filtered.empty else (None,None))
    st.sidebar.subheader("Georreferencia")
    lat_col = st.sidebar.selectbox("Columna Latitud", ["(auto)"] + list(filtered.columns), index=(filtered.columns.get_loc(lat_guess)+1 if (not filtered.empty and lat_guess in filtered.columns) else 0))
    lon_col = st.sidebar.selectbox("Columna Longitud", ["(auto)"] + list(filtered.columns), index=(filtered.columns.get_loc(lon_guess)+1 if (not filtered.empty and lon_guess in filtered.columns) else 0))
    if lat_col=="(auto)": lat_col=lat_guess
    if lon_col=="(auto)": lon_col=lon_guess

    st.sidebar.subheader("Mapa")
    map_mode        = st.sidebar.selectbox("Modo", ["Puntos","Heatmap","Hexágonos","Grilla"], index=0)
    color_dim_hint  = st.sidebar.text_input("Color por (categoría, opcional)", sector_col or "SECTOR")
    pt_size         = st.sidebar.slider("Tamaño de punto", 2, 80, 18, 1)
    pt_opacity      = st.sidebar.slider("Opacidad de punto", 10, 255, 220, 5)
    show_limits     = st.sidebar.checkbox("Mostrar límites", True)
    fill_limits     = st.sidebar.checkbox("Rellenar límites", False)
    limit_opacity   = st.sidebar.slider("Opacidad de límites", 10, 255, 80, 5)

    pts = pd.DataFrame()
    if lat_col and lon_col and not filtered.empty and lat_col in filtered.columns and lon_col in filtered.columns:
        pts = filtered[[lat_col, lon_col]].copy()
        pts[lat_col] = coerce_decimal(pts[lat_col])
        pts[lon_col] = coerce_decimal(pts[lon_col])
        pts = pts.rename(columns={lat_col:"lat", lon_col:"lon"}).dropna(subset=["lat","lon"])

    # límites
    gj=None
    if show_limits and os.path.exists(limite_path):
        try:
            with open(limite_path, "r", encoding="utf-8") as f:
                gj=json.load(f)
        except Exception as e:
            st.warning(f"No se pudo leer límites: {e}")

    # centro
    if not pts.empty:
        center_lat, center_lon = float(pts["lat"].median()), float(pts["lon"].median())
    elif gj is not None:
        center_lat, center_lon = geojson_center(gj)
    else:
        center_lat, center_lon = 13.494, -89.322

    # color por categoría
    color_dim = color_dim_hint if (color_dim_hint and color_dim_hint in filtered.columns) else (sector_col if sector_col in filtered.columns else None)
    color_df = None
    if color_dim and not pts.empty:
        color_df = filtered[[color_dim]].iloc[:len(pts)].copy().reset_index(drop=True)
        pts = pts.reset_index(drop=True)
        uniq = sorted(color_df[color_dim].dropna().astype(str).unique().tolist())
        def palette(n):
            base = [
                [31,120,180], [51,160,44], [227,26,28], [255,127,0], [106,61,154],
                [166,206,227], [178,223,138], [251,154,153], [253,191,111], [202,178,214],
                [255,255,153], [177,89,40]
            ]
            if n <= len(base): return base[:n]
            out = base.copy()
            while len(out)<n: out+=base
            return out[:n]
        pal = palette(len(uniq))
        colmap = {k: pal[i] for i, k in enumerate(uniq)}
        color_df["__color__"] = color_df[color_dim].astype(str).map(colmap)
        rgba = color_df["__color__"].apply(lambda x: x+[int(pt_opacity)])
        pts["c_r"] = rgba.apply(lambda v: v[0]); pts["c_g"]=rgba.apply(lambda v: v[1])
        pts["c_b"] = rgba.apply(lambda v: v[2]); pts["c_a"]=rgba.apply(lambda v: v[3])

    if _HAS_PYDECK and (gj is not None or not pts.empty):
        layers=[]
        if gj is not None:
            layers.append(pdk.Layer(
                "GeoJsonLayer",
                data=gj,
                stroked=True,
                filled=bool(fill_limits),
                get_line_color=[255, 255, 0, 255],
                get_line_width=3,
                get_fill_color=[255, 255, 0, int(limit_opacity)],
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
            layers.append(pdk.Layer("HeatmapLayer", data=pts, get_position="[lon, lat]", aggregation='"SUM"'))
        elif map_mode=="Hexágonos" and not pts.empty:
            layers.append(pdk.Layer("HexagonLayer", data=pts, get_position="[lon, lat]", radius=45, elevation_scale=6, extruded=True, coverage=1, pickable=True))
        elif map_mode=="Grilla" and not pts.empty:
            layers.append(pdk.Layer("GridLayer", data=pts, get_position="[lon, lat]", cell_size=60, extruded=False, pickable=True))
        st.subheader("Mapa")
        st.pydeck_chart(pdk.Deck(
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=12),
            map_style=None,
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

    # Tabla & descarga
    st.subheader("Tabla filtrada")
    st.dataframe(filtered, use_container_width=True, height=420)
    st.download_button("⬇️ Descargar CSV filtrado",
        data=filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name="filtrado.csv", mime="text/csv")

# ------------------------- TAB 2: Diccionario -------------------------
with tab2:
    st.title("Diccionario (Codebook)")
    if (df_vars is None or df_vars.empty) and not meta_lc:
        st.info("No se pudo mostrar el codebook (vacío o no cargado).")
    else:
        if df_vars is not None and not df_vars.empty:
            st.subheader("Variables")
            st.dataframe(df_vars.sort_values("variable"), use_container_width=True, height=420)
        vars_disponibles = sorted(list({*list(meta_lc.keys()), *[to_lower(v) for v in (df_vars["variable"] if df_vars is not None and not df_vars.empty else [])]}))
        var_sel = st.selectbox("Elige una variable", options=vars_disponibles)
        if var_sel:
            row = None
            if df_vars is not None and not df_vars.empty:
                row = df_vars[df_vars["variable"].astype(str).str.strip().str.lower()==var_sel]
            tipo = None; desc = None
            if row is not None and not row.empty:
                tipo = row["tipo"].iloc[0] if "tipo" in row.columns else None
                desc = row["descripcion"].iloc[0] if "descripcion" in row.columns else None
            st.write(f"**Variable:** `{var_sel}`")
            st.write(f"**Tipo:** {tipo if pd.notna(tipo) and tipo not in [None,'nan','None'] else '—'}")
            st.write(f"**Descripción:** {desc if pd.notna(desc) and desc not in [None,'nan','None'] else '—'}")
            mp = meta_lc.get(var_sel, {}).get("map", {})
            if mp:
                df_map = pd.DataFrame({"codigo": list(mp.keys()), "etiqueta": [mp[k] for k in mp.keys()]})
                st.dataframe(df_map, use_container_width=True, height=320)
                st.download_button("⬇️ Descargar mapeo CSV", df_map.to_csv(index=False).encode("utf-8-sig"), f"mapeo_{var_sel}.csv", "text/csv")
            else:
                st.caption("Esta variable no tiene mapeos categóricos registrados en el codebook.")

# ------------------------- TAB 3: Anexo Estadístico (por Sector/Bloque) -------------------------
with tab3:
    st.title("Plan de Tabulados y Cruces – Anexo Estadístico Final")
    st.caption("Filtra por Sector/Bloque y revisa tabulados simples y cruces por bloques B–I.")

    if df_display.empty:
        st.info("No hay datos cargados.")
        st.stop()

    # --------- Filtro por Sector/Bloque ---------
    def sector_column(df):
        for cand in ["SECTOR","sector","Sector","BLOQUE","bloque","Bloque"]:
            if cand in df.columns: return cand
        return None
    sector_col = sector_column(df_display)

    if sector_col:
        sectores = sorted(df_display[sector_col].dropna().astype(str).unique().tolist())
        sectors_pick = st.multiselect("Sector/Bloque", sectores, default=sectores)
    else:
        st.warning("No se detectó columna de Sector/Bloque. Renombra o indica la columna en el dataset.")
        sectors_pick = None

    df_anx = df_display.copy()
    if sector_col and sectors_pick:
        df_anx = df_anx[df_anx[sector_col].astype(str).isin(sectors_pick)]

    # --------- Helpers de análisis ----------
    def tab_simple(df, col, label=None):
        if col not in df.columns: 
            st.caption(f"• {label or col}: no disponible.")
            return
        s = df[col].dropna().astype(str)
        if s.empty:
            st.caption(f"• {label or col}: sin datos.")
            return
        freq = s.value_counts(dropna=False).rename("freq")
        pct = (freq / freq.sum() * 100).rename("pct")
        out = pd.concat([freq, pct.round(1)], axis=1)
        st.subheader(label or col)
        st.dataframe(out, use_container_width=True, height=300)
        st.download_button(f"⬇️ Descargar {label or col}", out.to_csv().encode("utf-8-sig"), f"tab_{col}.csv", "text/csv")

    def crosstab(df, rows, cols, normalize='index', label=None):
        if rows not in df.columns or cols not in df.columns:
            st.caption(f"• {label or (rows+' x '+cols)}: no disponible.")
            return
        x = pd.crosstab(df[rows].astype(str), df[cols].astype(str), normalize=normalize)*100
        x = x.round(1)
        st.subheader(label or f"{rows} × {cols} (%)")
        st.dataframe(x, use_container_width=True, height=360)
        st.download_button(f"⬇️ Descargar {label or (rows+'x'+cols)}", x.to_csv().encode("utf-8-sig"), f"cross_{rows}_{cols}.csv", "text/csv")

    def sumstats(df, cols, label=None):
        cols = [c for c in cols if c in df.columns]
        if not cols:
            st.caption(f"• {label or 'Estadísticos'}: columnas no disponibles.")
            return
        dd = df[cols].apply(pd.to_numeric, errors="coerce").describe(percentiles=[.25,.5,.75]).T
        dd["missing_%"] = (1 - df[cols].notna().mean()) * 100
        st.subheader(label or "Estadísticos")
        st.dataframe(dd.round(2), use_container_width=True, height=360)
        st.download_button(f"⬇️ Descargar {label or 'estadisticos'}", dd.to_csv().encode("utf-8-sig"), f"stats_{'_'.join(cols[:3])}.csv", "text/csv")

    # Heurística de p004 (uso)
    uso_col = "p004" if "p004" in df_anx.columns else next((c for c in df_anx.columns if to_lower(c) in ["uso","uso_estructura","p004_uso"]), None)
    def uso_cat(val):
        s = to_lower(val)
        if s in ["1","vivienda","residencial","hogar"]: return "vivienda"
        if s in ["2","negocio","comercial","empresa"]: return "negocio"
        if s in ["3","mixto","mixta","vivienda/negocio","residencial/comercial"]: return "mixto"
        if "vivi" in s: return "vivienda"
        if "nego" in s or "comer" in s or "emp" in s: return "negocio"
        if "mixt" in s: return "mixto"
        return val

    if uso_col:
        df_anx["_uso_norm"] = df_anx[uso_col].apply(uso_cat)
    else:
        df_anx["_uso_norm"] = np.nan

    # ===================== BLOQUE B – Físicas (todos) =====================
    st.header("BLOQUE B – Características físicas de la estructura")
    col_p005 = "p005" if "p005" in df_anx.columns else next((c for c in df_anx.columns if to_lower(c) in ["estado","estado_fisico","condicion","condición","p005_estado"]), None)
    col_p006 = "p006" if "p006" in df_anx.columns else next((c for c in df_anx.columns if "techo" in to_lower(c) or to_lower(c)=="p006_techo"), None)
    col_p007 = "p007" if "p007" in df_anx.columns else next((c for c in df_anx.columns if "pared" in to_lower(c) or to_lower(c)=="p007_paredes"), None)
    col_p008 = "p008" if "p008" in df_anx.columns else next((c for c in df_anx.columns if "piso" in to_lower(c) or to_lower(c)=="p008_piso"), None)

    tab_simple(df_anx, uso_col or "_uso_norm", "Uso de estructura (p004)")
    tab_simple(df_anx, col_p005, "Estado físico (p005)")
    tab_simple(df_anx, col_p006, "Material del techo (p006)")
    tab_simple(df_anx, col_p007, "Material de las paredes (p007)")
    tab_simple(df_anx, col_p008, "Material del piso (p008)")

    crosstab(df_anx, "_uso_norm", col_p005, label="p004 × p005 (Estado físico por uso)")
    for cc, name in [(col_p006,"techo (p006)"), (col_p007,"paredes (p007)"), (col_p008,"piso (p008)")]:
        crosstab(df_anx, col_p005, cc, label=f"p005 × {name}")
        crosstab(df_anx, "_uso_norm", cc, label=f"p004 × {name}")

    # Subconjunto para C–E: hogares dentro (vivienda o mixto)
    df_hh = df_anx[df_anx["_uso_norm"].isin(["vivienda","mixto"])].copy()

    # ===================== BLOQUE C – Hogares en estructura =====================
    st.header("BLOQUE C – Hogares dentro de la estructura")
    col_nvivienda = next((c for c in df_hh.columns if to_lower(c) in ["nvivienda","n_hogares","num_hogares","nro_hogares"]), None)
    col_p009a = "p009a" if "p009a" in df_hh.columns else next((c for c in df_hh.columns if "espacio" in to_lower(c) and "habita" in to_lower(c)), None)
    col_p009b = "p009b" if "p009b" in df_hh.columns else next((c for c in df_hh.columns if "nivel" in to_lower(c)), None)
    col_p010  = "p010"  if "p010"  in df_hh.columns else next((c for c in df_hh.columns if "tenencia" in to_lower(c) or "propiedad" in to_lower(c)), None)
    col_p011  = "p011"  if "p011"  in df_hh.columns else next((c for c in df_hh.columns if "personas"==to_lower(c) or to_lower(c) in ["tam_hogar","tamano_hogar","tamaño_hogar"]), None)

    # Sexo jefatura (heurística)
    col_sex_jef = next((c for c in df_hh.columns if "sexo" in to_lower(c) and "jef" in to_lower(c)), None)
    sex_m_ad = next((c for c in df_hh.columns if to_lower(c) in ["sexom","mujeres_adultas"]), None)
    sex_h_ad = next((c for c in df_hh.columns if to_lower(c) in ["sexoh","hombres_adultos"]), None)
    sex_nh   = next((c for c in df_hh.columns if to_lower(c) in ["sexonh","ninos","niños"]), None)
    sex_nm   = next((c for c in df_hh.columns if to_lower(c) in ["sexonm","ninas","niñas"]), None)

    # Tabulados simples
    sumstats(df_hh, [c for c in [col_nvivienda] if c], "Nº de hogares (nvivienda)")
    sumstats(df_hh, [c for c in [col_p009a, col_p009b] if c], "Espacios habitables (p009a) y Nº niveles (p009b)")
    tab_simple(df_hh, col_p010, "Tenencia del inmueble (p010)")
    if col_sex_jef: tab_simple(df_hh, col_sex_jef, "Sexo de la jefatura (derivado)")
    sumstats(df_hh, [c for c in [col_p011] if c], "Nº de personas (p011)")

    # Desagregados de personas si existen
    if any([sex_m_ad, sex_h_ad, sex_nh, sex_nm]):
        cols_people = [c for c in [sex_m_ad, sex_h_ad, sex_nh, sex_nm] if c]
        sumstats(df_hh, cols_people, "Composición: mujeres/hombres adultos y niños/niñas")

    # Cruces clave C
    if col_sex_jef and col_p010: crosstab(df_hh, col_sex_jef, col_p010, label="Sexo jefatura × Tenencia (p010)")
    for var, name in [(col_p005,"estado físico (p005)"), ("p015","servicios básicos (p015)"), ("p014","fuente de ingreso (p014)")]:
        if (var in df_hh.columns) and col_sex_jef:
            crosstab(df_hh, col_sex_jef, var, label=f"Sexo jefatura × {name}")
    if col_p011 and col_sex_jef:
        sumstats(df_hh.groupby(col_sex_jef)[col_p011].apply(pd.to_numeric, errors="coerce").reset_index(name=col_p011),
                 [col_p011], "Tamaño del hogar (p011) por sexo jefatura")

    # ===================== BLOQUE D – Socioeconómico =====================
    st.header("BLOQUE D – Situación socioeconómica del hogar")
    col_p012 = "p012" if "p012" in df_hh.columns else next((c for c in df_hh.columns if "residen" in to_lower(c) and "ano" in to_lower(c) or "año" in to_lower(c)), None)
    col_p013 = "p013" if "p013" in df_hh.columns else next((c for c in df_hh.columns if "ingres" in to_lower(c) and "person" in to_lower(c)), None)
    col_p014 = "p014" if "p014" in df_hh.columns else next((c for c in df_hh.columns if "fuente" in to_lower(c) and "ingreso" in to_lower(c)), None)
    col_p022 = "p022" if "p022" in df_hh.columns else next((c for c in df_hh.columns if "activo" in to_lower(c) and "hogar" in to_lower(c)), None)

    sumstats(df_hh, [c for c in [col_p012] if c], "Año de residencia (p012)")
    sumstats(df_hh, [c for c in [col_p013] if c], "Nº de personas con ingresos (p013)")
    if col_p014: tab_simple(df_hh, col_p014, "Fuente principal de ingreso (p014)")
    if col_p022: tab_simple(df_hh, col_p022, "Activos del hogar (p022)")

    if col_p014 and col_sex_jef: crosstab(df_hh, col_p014, col_sex_jef, label="Fuente de ingreso × Sexo jefatura")
    if col_p013 and col_p011:
        tmp = df_hh[[col_p013, col_p011]].apply(pd.to_numeric, errors="coerce").dropna()
        if not tmp.empty:
            tmp["q_tam"] = pd.qcut(tmp[col_p011], q=min(4, tmp[col_p011].nunique()), duplicates="drop")
            sumstats(tmp.groupby("q_tam")[col_p013].mean().reset_index(name=col_p013), [col_p013], "Promedio personas con ingresos por tamaño de hogar (cuartiles)")
    if col_p022 and col_p010: crosstab(df_hh, col_p022, col_p010, label="Activos × Tenencia")
    if col_p022 and ("p015" in df_hh.columns): crosstab(df_hh, col_p022, "p015", label="Activos × Servicios básicos")

    # ===================== BLOQUE E – Servicios y saneamiento =====================
    st.header("BLOQUE E – Acceso a servicios y saneamiento")
    for c,name in [("p015","Servicios básicos (p015)"),("p016","Frecuencia acceso agua (p016)"),
                   ("p017","Fuente de agua (p017)"),("p018","Tipo de sanitario (p018)"),
                   ("p019","Uso sanitario (p019)"),("p020","Eliminación aguas grises (p020)"),
                   ("p021","Eliminación basura (p021)")]:
        tab_simple(df_hh, c, name) if c in df_hh.columns else None

    if "p015" in df_hh.columns and col_p010: crosstab(df_hh, "p015", col_p010, label="Servicios básicos × Tenencia")
    if "p015" in df_hh.columns and col_sex_jef: crosstab(df_hh, "p015", col_sex_jef, label="Servicios básicos × Sexo jefatura")
    if "p015" in df_hh.columns and col_p005: crosstab(df_hh, "p015", col_p005, label="Servicios básicos × Estado físico")
    if "p016" in df_hh.columns and "p017" in df_hh.columns: crosstab(df_hh, "p016", "p017", label="Frecuencia acceso agua × Fuente de agua")
    if "p018" in df_hh.columns and "p019" in df_hh.columns: crosstab(df_hh, "p018", "p019", label="Tipo sanitario × Uso sanitario")
    if "p020" in df_hh.columns and "p021" in df_hh.columns: crosstab(df_hh, "p020", "p021", label="Aguas grises × Basura")

    # ===================== BLOQUE F – Negocios (p004 = negocio o mixto) =====================
    st.header("BLOQUE F – Negocios")
    df_neg = df_anx[df_anx["_uso_norm"].isin(["negocio","mixto"])].copy()
    for c,name in [("p025","Actividad principal (p025)"),("p027","Permisos de operación (p027)"),
                   ("p028","Tenencia local (p028)")]:
        tab_simple(df_neg, c, name) if c in df_neg.columns else None

    for c,name in [("p026","Tiempo de operación (p026)"),("p029","Nº trabajadores (p029)"),
                   ("p030","Nº empleados formales (p030)"),("p031","Ingreso mensual empleados (p031)")]:
        sumstats(df_neg, [c], name) if c in df_neg.columns else None

    if "p032" in df_neg.columns: tab_simple(df_neg, "p032", "Activos negocio (p032)")

    # Cruces F
    if "p025" in df_neg.columns and "p027" in df_neg.columns: crosstab(df_neg, "p025", "p027", label="Actividad × Permisos")
    if "p027" in df_neg.columns and "p028" in df_neg.columns: crosstab(df_neg, "p027", "p028", label="Permisos × Tenencia local")
    if "p030" in df_neg.columns and "p029" in df_neg.columns:
        tmp = df_neg[["p029","p030"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not tmp.empty:
            tmp["formales_%"] = np.where(tmp["p029"]>0, tmp["p030"]/tmp["p029"]*100, np.nan)
            sumstats(tmp, ["formales_%"], "Porcentaje de personal formalizado")
    if "p026" in df_neg.columns and "p027" in df_neg.columns: crosstab(df_neg, "p026", "p027", label="Tiempo operación × Permisos")
    if "p031" in df_neg.columns and "p027" in df_neg.columns: crosstab(df_neg, "p031", "p027", label="Ingreso mensual × Permisos")

    # ===================== BLOQUE G – Espacios públicos y percepción =====================
    st.header("BLOQUE G – Espacios públicos y percepción")
    for c,name in [("p036","Percepción de seguridad (p036)"),
                   ("p035","Condiciones del espacio (p035)"),
                   ("p035tx","Problemas identificados (p035tx)")]:
        tab_simple(df_anx, c, name) if c in df_anx.columns else None

    if "p036" in df_anx.columns and uso_col: crosstab(df_anx, "p036", "_uso_norm", label="Percepción seguridad × Uso de estructura")
    if "p036" in df_hh.columns and col_sex_jef: crosstab(df_hh, "p036", col_sex_jef, label="Percepción seguridad × Sexo jefatura")
    if "p035" in df_anx.columns and "p035tx" in df_anx.columns: crosstab(df_anx, "p035", "p035tx", label="Condiciones espacio × Problemas identificados")

    # ===================== BLOQUE H – Variables abiertas =====================
    st.header("BLOQUE H – Variables abiertas (otros/especifique)")
    st.caption("Sugerencia: exporta 'p035tx' u otras de texto para codificación temática externa si no hay categorías predefinidas.")
    if "p035tx" in df_anx.columns:
        muestras = df_anx["p035tx"].dropna().astype(str).unique().tolist()[:50]
        if muestras:
            st.write("Muestras de respuestas (hasta 50):")
            st.write(muestras)

    # ===================== BLOQUE I – Indicadores clave =====================
    st.header("BLOQUE I – Indicadores clave")
    def ratio_bool(df, col, truth=("1","si","sí","true","yes")):
        if col not in df.columns or df[col].dropna().empty: return np.nan
        s = df[col].astype(str).str.strip().str.lower()
        return s.isin(truth).mean()*100

    ind = {}
    # % estructuras en mal estado (por p005 conteniendo 'malo'/'deficiente')
    if col_p005:
        s = df_anx[col_p005].astype(str).str.lower()
        ind["% estructuras en mal estado"] = (s.str.contains("mal") | s.str.contains("defici")).mean()*100
    # % hogares con jefatura femenina
    if col_sex_jef:
        s = df_hh[col_sex_jef].astype(str).str.lower()
        ind["% hogares con jefatura femenina"] = s.str_contains("fem").mean()*100 if hasattr(s, "str_contains") else s.str.contains("fem").mean()*100
    # % hogares con tenencia precaria (heurística: arriendo informal/cedido/ocupación)
    if col_p010:
        s = df_hh[col_p010].astype(str).str.lower()
        ind["% tenencia precaria"] = s.str.contains("precari|ocup|cedid|informal|invad").mean()*100
    # % hogares sin acceso a agua potable (p015)
    if "p015" in df_hh.columns:
        s = df_hh["p015"].astype(str).str.lower()
        ind["% hogares sin agua potable"] = (~s.str.contains("agua|acueduct|potab")).mean()*100
    # % hogares con saneamiento inadecuado (p018/p019)
    if "p018" in df_hh.columns:
        s = df_hh["p018"].astype(str).str.lower()
        ind["% saneamiento inadecuado"] = s.str.contains("ningun|ningún|letrina|impro").mean()*100
    # % negocios sin permisos
    if "p027" in df_neg.columns:
        s = df_neg["p027"].astype(str).str.lower()
        ind["% negocios sin permisos"] = (~s.str.contains("si|sí|permiso")).mean()*100
    # Promedio activos por hogar
    if "p022" in df_hh.columns:
        ind["Promedio activos por hogar"] = pd.to_numeric(df_hh["p022"], errors="coerce").mean()
    # % negocios con personal formalizado
    if "p029" in df_neg.columns and "p030" in df_neg.columns:
        tmp = df_neg[["p029","p030"]].apply(pd.to_numeric, errors="coerce").dropna()
        if not tmp.empty:
            ind["% negocios con personal formalizado"] = (np.where(tmp["p029"]>0, tmp["p030"]/tmp["p029"], np.nan).mean())*100

    if ind:
        card_cols = st.columns(min(4, len(ind)))
        i=0
        for k,v in ind.items():
            with card_cols[i%len(card_cols)]:
                if isinstance(v, (int,float)) and not pd.isna(v):
                    if 'Promedio' in k:
                        st.metric(k, f"{v:.1f}")
                    else:
                        st.metric(k, f"{v:.1f}%")
                else:
                    st.metric(k, "—")
            i+=1
    else:
        st.caption("No fue posible calcular indicadores (faltan variables).")
