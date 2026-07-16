"""
Limpieza del resultado final (ETL excepcional).

Basado en notebooks/REVISION_FINAL.ipynb:
  - enriquecer con reporte_total.json (remitente, asunto, n_doc→nombre_informe)
  - excluir remitente vacío
  - deduplicar hoja_ruta
  - limpiar asunto desde la palabra 'proyecto'
  - exportar CSV de entrega

Tipos:
  - expediente_final: inventario en expediente_final/
  - oficios: reporte_solicitud_oficio.csv en expedientes_oficio/
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import (
    cargar_ocr_ficha,
    cargar_tabla,
    limpiar_inventario_dataframe,
    limpiar_inventario_oficios,
)


def ejecutar(config, logger, tipo="oficios", entrada=None, salida=None, reporte_json=None):
    tipo = str(tipo or "oficios").strip().lower()
    if tipo not in {"oficios", "expediente_final", "ambos"}:
        raise ValueError("tipo debe ser: oficios | expediente_final | ambos")

    resultados = []
    if tipo in {"oficios", "ambos"}:
        resultados.append(
            _limpiar_oficios(config, logger, entrada=entrada, salida=salida, reporte_json=reporte_json)
        )
    if tipo in {"expediente_final", "ambos"}:
        # En 'ambos', no reutilizar --entrada/--salida del oficios.
        resultados.append(
            _limpiar_expediente_final(
                config,
                logger,
                entrada=None if tipo == "ambos" else entrada,
                salida=None if tipo == "ambos" else salida,
                reporte_json=reporte_json,
            )
        )

    if len(resultados) == 1:
        return resultados[0]
    return {
        "proceso": "limpiar_resultado_final",
        "estado": "completado",
        "tipos": resultados,
    }


def _ruta_ocr(config, reporte_json):
    if reporte_json:
        return Path(reporte_json)
    return Path(config["paths"]["work"]["ocr_fichas"]["reportes"]) / "reporte_total.json"


def _limpiar_oficios(config, logger, entrada=None, salida=None, reporte_json=None):
    rf = config["paths"]["output"]["resultados_finales"]
    root = Path(rf.get("expedientes_oficio") or (Path(rf["inventario"]).parent / "expedientes_oficio"))
    entrada = Path(entrada or (root / "reporte_solicitud_oficio.csv"))
    salida = Path(salida or (root / "reporte_solicitud_oficio_limpio.csv"))
    ocr_path = _ruta_ocr(config, reporte_json)

    logger.info("Limpieza oficios: %s → %s", entrada, salida)
    df = cargar_tabla(entrada)
    if "hoja_ruta" not in df.columns:
        raise KeyError(f"Sin columna hoja_ruta en {entrada}")

    # Solo filas con match si existe la columna.
    if "match_oficio" in df.columns:
        antes = len(df)
        df = df[df["match_oficio"].astype(str).str.strip().str.lower().isin({"true", "1", "si", "sí"})].copy()
        logger.info("Filtrado match_oficio=True: %s → %s", antes, len(df))

    ocr = cargar_ocr_ficha(ocr_path)
    # Entrega flujo 2: hoja_ruta | oficio (archivo) | remitente | asunto
    limpio = limpiar_inventario_oficios(df, ocr)

    asegurar_directorio(salida.parent)
    limpio.to_csv(salida, index=False, encoding="utf-8-sig")

    resumen = {
        "proceso": "limpiar_resultado_final",
        "tipo": "oficios",
        "estado": "completado",
        "columnas": list(limpio.columns),
        "entrada": str(entrada),
        "filas_entrada": len(df),
        "filas_salida": len(limpio),
        "salida": str(salida),
        "fuente_ocr": str(ocr_path),
    }
    logger.info("Limpieza oficios OK: %s", resumen)
    return resumen


def _limpiar_expediente_final(config, logger, entrada=None, salida=None, reporte_json=None):
    rf = config["paths"]["output"]["resultados_finales"]
    root = Path(rf.get("expediente_final") or (Path(rf["inventario"]).parent / "expediente_final"))

    if entrada:
        entrada = Path(entrada)
    else:
        # Preferir xlsx del notebook; si no, csv del merge.
        candidatos = [
            root / "inventario_final.xlsx",
            root / "inventario_final_limpio.csv",
            root / "inventario_final.csv",
        ]
        entrada = next((p for p in candidatos if p.exists()), candidatos[0])

    salida = Path(salida or (root / "inventario_final_alimentado_limpio.csv"))
    ocr_path = _ruta_ocr(config, reporte_json)

    logger.info("Limpieza expediente_final: %s → %s", entrada, salida)
    df = cargar_tabla(entrada)
    if "hoja_ruta" not in df.columns:
        raise KeyError(f"Sin columna hoja_ruta en {entrada}")

    ocr = cargar_ocr_ficha(ocr_path)
    limpio = limpiar_inventario_dataframe(df, ocr)

    asegurar_directorio(salida.parent)
    limpio.to_csv(salida, index=False, encoding="utf-8-sig")

    resumen = {
        "proceso": "limpiar_resultado_final",
        "tipo": "expediente_final",
        "estado": "completado",
        "entrada": str(entrada),
        "filas_entrada": len(df),
        "filas_salida": len(limpio),
        "salida": str(salida),
        "fuente_ocr": str(ocr_path),
    }
    logger.info("Limpieza expediente_final OK: %s", resumen)
    return resumen
