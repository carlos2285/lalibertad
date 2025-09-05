# Puerto de La Libertad — Dashboard de Estructuras y Hogares

Dashboard en **Streamlit** para explorar estructuras del territorio y hogares (subset), con soporte de **codebook** para tipificar y decodificar variables.

## Estructura del proyecto

```
.
├─ app.py                 # único entrypoint del dashboard
├─ requirements.txt
├─ .gitignore
├─ .gitattributes         # Git LFS para xlsx y GIS
├─ .streamlit/
│  └─ config.toml
└─ data/
   ├─ private/
   │  ├─ basedarboard.xlsx     # estructuras (completo)
   │  └─ hogares.xlsx          # hogares (completo)
   ├─ metadata/
   │  └─ Codebook.xlsx         # codebook (completo)
   ├─ sample/
   │  ├─ estructuras_sample.csv
   │  └─ hogares_sample.csv
   └─ gis/
      └─ limites.geojson       # opcional
```

## Requisitos

- Python 3.10+
- Paquetes: ver `requirements.txt`
- Para versionar archivos grandes: **Git LFS**

## Instalación

```bash
pip install -r requirements.txt
```

### (Opcional) Habilitar Git LFS

```bash
git lfs install
git lfs track "*.xlsx"
git add .gitattributes
git add data/private/*.xlsx data/metadata/Codebook.xlsx
git commit -m "data: subir Excel completos via LFS"
```

## Ejecución local

```bash
streamlit run app.py
```

La app leerá por defecto:
- `data/metadata/Codebook.xlsx`
- `data/private/basedarboard.xlsx`
- `data/private/hogares.xlsx`

Puedes cambiar rutas en la barra lateral si lo deseas.

## Despliegue (Streamlit Cloud)

1. Haz push de este repo a GitHub.
2. En Streamlit Cloud, selecciona `app.py` como archivo principal.
3. Define `Python version` y que instale con `requirements.txt`.
4. (Si usas datos privados) marca el repo como **private** o usa Secrets/Storage según tu política.

## Notas del Codebook

- El dashboard intenta detectar:
  - **Tipos** sugeridos (numérico, fecha, categórico).
  - **Mapeos** de valores (p. ej. 1=Hogar, 0=No hogar).
- Columnas típicas que se usan como filtros: Departamento, Municipio, Distrito, Sector, es_hogar.
- Mapa: se detectan heurísticamente columnas `lat/lon` (`lat`, `lon`, `latitude`, `longitude`, `p002__Latitude`, `p002__Longitude`, etc.).

## Soporte / TODOs

- Añadir capas de mapa por categorías (pydeck) — opcional.
- Panel de calidad de datos (% faltantes, duplicados por clave).
- Exportar PDF/PowerPoint con KPIs — opcional.