"""Crea carpetas OFICIOS/{empresa}/{COMPLETO|PARCIAL|INCOMPLETO} desde pestaña EMPRESAS."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from utils.empresas.carpetas_oficios import CARPETA_POR_EMPRESA, ESTADOS_OFICIO, nombre_carpeta_empresa
from utils.empresas.normalizar_remitente import CANONICOS_REMITENTE

CANONICOS_NOMBRES = {nombre for nombre, _ in CANONICOS_REMITENTE}


def ejecutar(config, logger, xlsx=None, destino=None, dry_run=False, solo_mapeadas=True):
    rutas = config["paths"]
    tablas = Path(rutas["work"]["analisis"]["tablas"])
    xlsx = Path(xlsx or (tablas / "seleccionados_total.xlsx"))
    destino = Path(destino or rutas["input"]["oficios"])

    if not xlsx.exists():
        raise FileNotFoundError(f"No existe {xlsx}")
    if "oficios" not in rutas.get("input", {}):
        raise KeyError("Config sin paths.input.oficios")

    empresas = pd.read_excel(xlsx, sheet_name="EMPRESAS")
    if "empresa" not in empresas.columns:
        raise KeyError(f"La hoja EMPRESAS de {xlsx} no tiene columna 'empresa'")

    creadas = 0
    existentes = 0
    omitidas = 0
    detalle = []

    for empresa in empresas["empresa"].fillna("").astype(str).str.strip():
        if not empresa:
            continue
        if solo_mapeadas and empresa not in CANONICOS_NOMBRES and empresa not in CARPETA_POR_EMPRESA:
            omitidas += 1
            logger.warning("Empresa omitida (no canonica/mapeada): %s", empresa)
            continue

        carpeta = nombre_carpeta_empresa(empresa)
        # Evitar nombres de carpeta inválidos en Windows (caracteres de control/OCR).
        if any(ch in carpeta for ch in '<>:"/\\|?*') or len(carpeta) < 3:
            omitidas += 1
            logger.warning("Empresa omitida (nombre invalido): %s", empresa)
            continue

        base = destino / carpeta
        if dry_run:
            logger.info("[dry-run] %s", base)
            detalle.append(str(base))
            creadas += 1
            continue

        if base.exists():
            existentes += 1
        else:
            base.mkdir(parents=True, exist_ok=True)
            creadas += 1

        for estado in ESTADOS_OFICIO:
            (base / estado).mkdir(parents=True, exist_ok=True)
        detalle.append(str(base))

    resumen = {
        "proceso": "crear_carpetas_empresas",
        "estado": "completado",
        "xlsx": str(xlsx),
        "destino": str(destino),
        "empresas_en_hoja": int(empresas["empresa"].fillna("").astype(str).str.strip().astype(bool).sum()),
        "carpetas_creadas": creadas,
        "carpetas_ya_existentes": existentes,
        "omitidas": omitidas,
        "dry_run": dry_run,
    }
    logger.info("Carpetas OFICIOS: %s", resumen)
    return resumen
