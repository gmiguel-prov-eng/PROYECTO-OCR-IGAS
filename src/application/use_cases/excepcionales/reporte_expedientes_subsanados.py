"""
Genera inventario CSV de expedientes en revision_manual (PDF analizados / revisados).

Columnas: hoja_ruta, nombre_informe, remitente, asunto.
hoja_ruta = nombre del PDF; el resto desde reporte_total.json (n_doc -> nombre_informe).
asunto se limpia dejando desde la palabra 'proyecto'.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio

COLUMNAS_REPORTE = ["hoja_ruta", "nombre_informe", "remitente", "asunto"]

PATRON_ASUNTO = re.compile(r"^.*?(?=\bproyecto\b)", flags=re.IGNORECASE)


def normalizar_hoja_ruta(texto):
    texto = str(texto or "").upper()
    return re.sub(r"[^A-Z0-9]", "", texto)


def limpiar_asunto(texto):
    texto = str(texto or "").strip()
    if not texto or texto.lower() in {"nan", "none"}:
        return ""
    limpio = PATRON_ASUNTO.sub("", texto).strip()
    return limpio


def ejecutar(config, logger, pdfs_dir=None, reporte_json=None, salida_dir=None):
    rutas = config["paths"]
    resultados = Path(rutas["output"]["resultados_finales"]["resultados"]).parent

    pdfs_dir = Path(pdfs_dir or (resultados / "expediente_subsanados"))
    salida_dir = Path(salida_dir or pdfs_dir)
    reporte_json = Path(
        reporte_json
        or Path(rutas["work"]["ocr_fichas"]["reportes"]) / "reporte_total.json"
    )

    if not pdfs_dir.exists():
        raise FileNotFoundError(f"No existe carpeta de PDF: {pdfs_dir}")
    if not reporte_json.exists():
        raise FileNotFoundError(f"No existe reporte JSON: {reporte_json}")

    asegurar_directorio(salida_dir)

    logger.info("PDFs subsanados: %s", pdfs_dir)
    logger.info("Fuente OCR JSON: %s", reporte_json)

    pdfs = sorted(p for p in pdfs_dir.glob("*.pdf") if p.is_file())
    if not pdfs:
        raise FileNotFoundError(f"No hay PDF en {pdfs_dir}")

    ocr = pd.read_json(reporte_json, orient="records")
    if "hoja_ruta" not in ocr.columns:
        raise KeyError("reporte_total.json no tiene columna hoja_ruta")

    ocr = ocr.copy()
    ocr["_key"] = ocr["hoja_ruta"].map(normalizar_hoja_ruta)
    ocr_unico = ocr[ocr["_key"].astype(bool)].drop_duplicates(subset=["_key"], keep="first")
    por_key = {str(r["_key"]): r for _, r in ocr_unico.iterrows()}

    por_archivo = {}
    if "archivo_pdf" in ocr.columns:
        for _, r in ocr.iterrows():
            stem = Path(str(r.get("archivo_pdf") or "")).stem
            key = normalizar_hoja_ruta(stem)
            if key and key not in por_archivo:
                por_archivo[key] = r

    registros = []
    con_match = 0
    sin_match = 0

    for pdf in pdfs:
        hoja = pdf.stem.strip()
        key = normalizar_hoja_ruta(hoja)
        fuente = por_key.get(key)
        if fuente is None:
            fuente = por_archivo.get(key)

        fila = {
            "hoja_ruta": hoja,
            "nombre_informe": "",
            "remitente": "",
            "asunto": "",
        }

        if fuente is not None:
            con_match += 1
            hr_ocr = str(fuente.get("hoja_ruta") or "").strip()
            if hr_ocr:
                fila["hoja_ruta"] = hr_ocr
            fila["nombre_informe"] = str(fuente.get("n_doc") or "").strip()
            fila["remitente"] = str(fuente.get("remitente") or "").strip()
            fila["asunto"] = limpiar_asunto(fuente.get("asunto"))
            for campo in ("nombre_informe", "remitente"):
                if fila[campo].lower() in {"nan", "none"}:
                    fila[campo] = ""
        else:
            sin_match += 1

        registros.append(fila)

    reporte = pd.DataFrame(registros, columns=COLUMNAS_REPORTE).fillna("")
    csv_path = salida_dir / "inventario_subsanados.csv"
    reporte.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # Limpia salidas antiguas si existen.
    for extra in ("inventario_subsanados.json", "inventario_subsanados.xlsx"):
        viejo = salida_dir / extra
        if viejo.exists():
            try:
                viejo.unlink()
            except OSError:
                logger.warning("No se pudo eliminar %s", viejo)

    resumen = {
        "proceso": "reporte_expedientes_subsanados",
        "estado": "completado",
        "pdfs": len(pdfs),
        "con_datos_ocr": con_match,
        "sin_datos_ocr": sin_match,
        "fuente_json": str(reporte_json),
        "csv": str(csv_path),
    }
    logger.info("Inventario revision_manual/subsanados: %s", resumen)
    return resumen
