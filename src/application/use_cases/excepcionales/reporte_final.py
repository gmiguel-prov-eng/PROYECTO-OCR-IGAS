"""
Genera un Excel 'reporte_final' con dos pestañas:

  1) inventario_final   -> tabla de inventario final de entrega
                           (por defecto: inventario_lote_2.xlsx)
  2) solicitudes_sueltas -> solicitudes sin oficio ni expediente armado
                           (work/05_fichas_oficios/reportes/solicitudes_sueltas.csv)

Salida (por defecto):
  data/output/04_resultados_finales/reporte_final.xlsx
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import cargar_tabla


def _root_resultados(config) -> Path:
    rf = config["paths"]["output"]["resultados_finales"]
    return Path(rf["inventario"]).parent


def ejecutar(config, logger, inventario=None, sueltas=None, salida=None):
    root = _root_resultados(config)
    reportes_oficios = Path(config["paths"]["work"]["fichas_oficios"]["reportes"])

    ruta_inventario = Path(inventario) if inventario else (root / "inventario_lote_2.xlsx")
    ruta_sueltas = Path(sueltas) if sueltas else (reportes_oficios / "solicitudes_sueltas.csv")

    if not ruta_inventario.exists():
        raise FileNotFoundError(f"No existe el inventario final: {ruta_inventario}")
    if not ruta_sueltas.exists():
        raise FileNotFoundError(f"No existe solicitudes_sueltas: {ruta_sueltas}")

    logger.info("Inventario final: %s", ruta_inventario)
    logger.info("Solicitudes sueltas: %s", ruta_sueltas)

    df_inventario = cargar_tabla(ruta_inventario)
    df_sueltas = cargar_tabla(ruta_sueltas)

    salida = Path(salida or (root / "reporte_final.xlsx"))
    asegurar_directorio(salida.parent)

    with pd.ExcelWriter(salida, engine="openpyxl") as writer:
        df_inventario.to_excel(writer, index=False, sheet_name="inventario_final")
        df_sueltas.to_excel(writer, index=False, sheet_name="solicitudes_sueltas")

    resumen = {
        "proceso": "reporte_final",
        "estado": "completado",
        "filas_inventario_final": len(df_inventario),
        "filas_solicitudes_sueltas": len(df_sueltas),
        "hojas": ["inventario_final", "solicitudes_sueltas"],
        "salida": str(salida),
    }
    logger.info("Reporte final OK: %s", resumen)
    return resumen
