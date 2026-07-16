"""
Une inventarios limpios de entrega en un Excel de lote (Drive / Colab).

Fuentes:
  - expediente_final/inventario_final_alimentado_limpio.csv  → informe
  - expediente_subsanados/inventario_subsanados.csv          → informe
  - expedientes_oficio/reporte_solicitud_oficio_limpio.csv  → oficio

Salida (por defecto):
  data/output/04_resultados_finales/inventario_lote_2.xlsx
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import cargar_tabla, consolidar_inventario_lote


def _root_resultados(config) -> Path:
    rf = config["paths"]["output"]["resultados_finales"]
    return Path(rf["inventario"]).parent


def _cargar_si_existe(ruta: Path, logger, etiqueta: str):
    if not ruta.exists():
        logger.warning("Fuente %s no encontrada (se omite): %s", etiqueta, ruta)
        return None
    logger.info("Cargando %s: %s", etiqueta, ruta)
    return cargar_tabla(ruta)


def ejecutar(config, logger, salida=None, nombre_hoja="INVENTARIO"):
    root = _root_resultados(config)
    rf = config["paths"]["output"]["resultados_finales"]

    ruta_f1 = Path(rf.get("expediente_final") or (root / "expediente_final")) / (
        "inventario_final_alimentado_limpio.csv"
    )
    ruta_sub = Path(rf.get("expediente_subsanados") or (root / "expediente_subsanados")) / (
        "inventario_subsanados.csv"
    )
    ruta_of = Path(rf.get("expedientes_oficio") or (root / "expedientes_oficio")) / (
        "reporte_solicitud_oficio_limpio.csv"
    )

    dfs = {
        "expediente_final": _cargar_si_existe(ruta_f1, logger, "expediente_final"),
        "oficios": _cargar_si_existe(ruta_of, logger, "oficios"),
        "subsanados": _cargar_si_existe(ruta_sub, logger, "subsanados"),
    }
    presentes = {k: v for k, v in dfs.items() if v is not None}
    if not presentes:
        raise FileNotFoundError(
            "No hay ninguna de las 3 tablas limpieas "
            "(inventario_final_alimentado_limpio / inventario_subsanados / "
            "reporte_solicitud_oficio_limpio)."
        )

    inventario = consolidar_inventario_lote(presentes)

    salida = Path(salida or (root / "inventario_lote_2.xlsx"))
    asegurar_directorio(salida.parent)
    inventario.to_excel(salida, index=False, sheet_name=nombre_hoja or "INVENTARIO")

    resumen = {
        "proceso": "consolidar_inventario_lote",
        "estado": "completado",
        "fuentes": {k: len(v) for k, v in presentes.items()},
        "filas_salida": len(inventario),
        "columnas": list(inventario.columns),
        "salida": str(salida),
        "proyecto_url": "en blanco (completar tras Drive/Colab)",
    }
    logger.info("Inventario lote OK: %s", resumen)
    return resumen
