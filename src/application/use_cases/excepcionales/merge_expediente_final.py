"""
Limpieza final ETL: consolida PDF e inventarios de lotes en expediente_final.

Salida típica:
  expediente_final/
    pdfs/
    inventario_final.csv
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import cargar_tabla, normalizar_hoja_ruta, obtener_hojas_ruta


def cargar_inventario(inventario_path):
    inventario_path = Path(inventario_path)
    if not inventario_path.exists():
        inventario_xlsx = inventario_path.with_suffix(".xlsx")
        if inventario_xlsx.exists():
            inventario_path = inventario_xlsx
        else:
            return None

    try:
        return cargar_tabla(inventario_path)
    except Exception:
        return None


def copiar_pdf_plano(src_root, dst_root, lote, hojas_ruta=None):
    src_root = Path(src_root)
    dst_root = Path(dst_root)
    asegurar_directorio(dst_root)

    copied = 0
    hojas_norm = {normalizar_hoja_ruta(h) for h in (hojas_ruta or []) if h}

    for pdf_path in sorted(src_root.rglob("*.pdf")):
        if not pdf_path.is_file():
            continue

        if hojas_norm:
            relative_text = normalizar_hoja_ruta(str(pdf_path.relative_to(src_root)))
            if not any(hoja in relative_text for hoja in hojas_norm):
                continue

        destino = dst_root / pdf_path.name
        if destino.exists():
            destino = dst_root / f"{lote}_{pdf_path.name}"
        shutil.copy2(pdf_path, destino)
        copied += 1

    return copied


def guardar_json(data, path):
    path = Path(path)
    asegurar_directorio(path.parent)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def ejecutar(config, logger, desde=1, hasta=4, limpiar_pdfs=True):
    rutas = config["paths"]["output"]["resultados_finales"]
    expedientes_root = Path(rutas["expedientes"])
    inventario_root = Path(rutas["inventario"])

    if "expediente_final" in rutas:
        output_root = Path(rutas["expediente_final"])
    else:
        output_root = Path(rutas["expedientes"]).parent / "expediente_final"

    # Fuentes de revisión históricas en disco (carpeta legacy `resultados/`, sin path en YAML).
    output_base = Path(rutas["inventario"]).parent if "inventario" in rutas else output_root.parent
    resultados_legacy = output_base / "resultados"

    output_pdfs = output_root / "pdfs"
    asegurar_directorio(output_root)
    if limpiar_pdfs and output_pdfs.exists():
        shutil.rmtree(output_pdfs)
    asegurar_directorio(output_pdfs)

    inventarios = []
    total_pdfs = 0
    total_lotes = 0

    logger.info("Merge expediente_final lotes %s–%s → %s", desde, hasta, output_root)

    for lote_num in range(int(desde), int(hasta) + 1):
        lote_nombre = f"lote_{lote_num}"
        total_lotes += 1

        # Preferencia: export revision_expedientes → luego expedientes del lote.
        revision_root = resultados_legacy / "revision_expedientes" / lote_nombre
        expedientes_dir = revision_root / "expedientes"
        inventario_path = revision_root / "inventario_final.csv"

        if not expedientes_dir.exists():
            expedientes_dir = expedientes_root / lote_nombre
            if not expedientes_dir.exists():
                expedientes_dir = expedientes_root

        if not inventario_path.exists():
            inventario_path = inventario_root / lote_nombre / "inventario_final.csv"
            if not inventario_path.exists():
                inventario_path = inventario_root / "inventario_final.csv"

        inventario = cargar_inventario(inventario_path)
        hojas_ruta = set()
        if inventario is not None:
            inventarios.append(inventario)
            hojas_ruta = obtener_hojas_ruta(inventario)
            logger.info("%s: inventario %s filas", lote_nombre, len(inventario))
        else:
            logger.warning("%s: sin inventario en %s", lote_nombre, inventario_path)

        if expedientes_dir.exists():
            pdfs = copiar_pdf_plano(expedientes_dir, output_pdfs, lote_nombre, hojas_ruta=hojas_ruta or None)
            total_pdfs += pdfs
            logger.info("%s: %s PDF → pdfs/", lote_nombre, pdfs)
        else:
            logger.warning("%s: sin carpeta expedientes %s", lote_nombre, expedientes_dir)

    inventario_destino = output_root / "inventario_final.csv"
    if inventarios:
        inventario_final = pd.concat(inventarios, ignore_index=True)
        inventario_final.to_csv(inventario_destino, index=False, encoding="utf-8-sig")
        inventario_final.to_csv(output_root / "inventario_final_limpio.csv", index=False, encoding="utf-8-sig")
        guardar_json(
            inventario_final.to_dict(orient="records"),
            output_root / "inventario_final_limpio.json",
        )
        logger.info("Inventario consolidado: %s", inventario_destino)
    else:
        logger.warning("Sin inventarios para consolidar")

    return {
        "proceso": "merge_expediente_final",
        "estado": "completado",
        "lotes": total_lotes,
        "pdfs": total_pdfs,
        "salida": str(output_root),
        "inventario": str(inventario_destino) if inventarios else "",
    }
