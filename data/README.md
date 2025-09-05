# Carpeta `data/`

## Subcarpetas
- `data/private/`: **Excel completos** de estructuras (`basedarboard.xlsx`) y hogares (`hogares.xlsx`). Versionados con Git LFS.
- `data/metadata/`: `Codebook.xlsx` completo.
- `data/sample/`: muestras públicas pequeñas (`*_sample.csv`) para pruebas locales sin datos sensibles.
- `data/gis/`: capas geográficas ligeras (`limites.geojson`). Evita subir shapefiles completos.

## Notas
- Si el repo es público, considera dejar solo `sample/` y mover completos a otro remoto privado.
- Si mantienes completos aquí, usa Git LFS (ya configurado en `.gitattributes`).