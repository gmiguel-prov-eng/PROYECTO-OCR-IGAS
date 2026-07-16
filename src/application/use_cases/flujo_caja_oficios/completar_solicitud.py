from pathlib import Path

import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import normalizar_hoja_ruta

ESTADOS_EXCLUIDOS = {"INCOMPLETO", "PARCIAL"}


def ejecutar(config, logger):
    rutas = config["paths"]["work"]
    tablas = Path(rutas["analisis"]["tablas"])
    reportes_oficios = Path(rutas["fichas_oficios"]["reportes"])

    ruta_solicitudes = _resolver_entrada(
        tablas,
        "seleccionados_total",
        preferidas=(".xlsx", ".xls", ".csv"),
    )
    ruta_oficios = _resolver_entrada(
        reportes_oficios,
        "lista_fichas_oficios",
        preferidas=(".xlsx", ".xls", ".csv"),
    )
    salida = reportes_oficios / "solicitud_oficio.csv"

    asegurar_directorio(reportes_oficios)

    logger.info("Solicitudes: %s", ruta_solicitudes)
    logger.info("Oficios: %s", ruta_oficios)

    df_solicitudes = cargar_tabla(ruta_solicitudes)
    df_oficios = cargar_tabla(ruta_oficios)

    if "hoja_ruta" not in df_solicitudes.columns:
        raise KeyError(f"No existe columna 'hoja_ruta' en {ruta_solicitudes}")
    if "hoja_ruta" not in df_oficios.columns:
        raise KeyError(f"No existe columna 'hoja_ruta' en {ruta_oficios}")

    oficios_elegibles, oficios_excluidos = filtrar_oficios_para_merge(df_oficios, logger)
    df_result = merge_solicitud_oficio(df_solicitudes, oficios_elegibles, logger)
    df_result.to_csv(salida, index=False, encoding="utf-8-sig")

    # Validacion de los 2 contenidos evaluados (reportes aparte, no tocan inventario final):
    #   - solicitudes sin oficio: filas del cruce con match_oficio=False
    #   - oficios sin solicitud: oficios elegibles cuya hoja_ruta no aparece en solicitudes
    if "match_oficio" in df_result.columns:
        sin_oficio = df_result[~df_result["match_oficio"].astype(bool)].copy()
    else:
        sin_oficio = df_result.iloc[0:0].copy()
    salida_sin_oficio = reportes_oficios / "solicitudes_sin_oficio.csv"
    sin_oficio.to_csv(salida_sin_oficio, index=False, encoding="utf-8-sig")

    sin_solicitud = calcular_oficios_sin_solicitud(df_solicitudes, oficios_elegibles)
    salida_sin_solicitud = reportes_oficios / "oficios_sin_solicitud.csv"
    sin_solicitud.to_csv(salida_sin_solicitud, index=False, encoding="utf-8-sig")

    con_oficio = int(df_result["match_oficio"].sum()) if "match_oficio" in df_result.columns else 0
    oficios_con_hoja = int(
        oficios_elegibles["hoja_ruta"].map(normalizar_hoja_ruta).astype(bool).sum()
    )
    oficios_con_solicitud = oficios_con_hoja - len(sin_solicitud)

    # Resumen de validacion: demuestra en una sola tabla lo encontrado / no encontrado.
    resumen_validacion = pd.DataFrame(
        [
            ("solicitudes_total", len(df_solicitudes)),
            ("solicitudes_con_oficio", con_oficio),
            ("solicitudes_sin_oficio", len(sin_oficio)),
            ("oficios_entrada", len(df_oficios)),
            ("oficios_excluidos_parcial_incompleto_visto", oficios_excluidos),
            ("oficios_elegibles", len(oficios_elegibles)),
            ("oficios_con_solicitud", oficios_con_solicitud),
            ("oficios_sin_solicitud", len(sin_solicitud)),
        ],
        columns=["metrica", "valor"],
    )
    salida_resumen = reportes_oficios / "resumen_validacion.csv"
    resumen_validacion.to_csv(salida_resumen, index=False, encoding="utf-8-sig")

    resumen = {
        "proceso": "completar_solicitud",
        "estado": "completado",
        "solicitudes": len(df_solicitudes),
        "oficios_entrada": len(df_oficios),
        "oficios_excluidos": oficios_excluidos,
        "oficios_usados": len(oficios_elegibles),
        "filas_resultado": len(df_result),
        "con_match_oficio": con_oficio,
        "sin_match_oficio": len(df_result) - con_oficio,
        "solicitudes_sin_oficio": len(sin_oficio),
        "oficios_con_solicitud": oficios_con_solicitud,
        "oficios_sin_solicitud": len(sin_solicitud),
        "salida": str(salida),
        "salida_solicitudes_sin_oficio": str(salida_sin_oficio),
        "salida_oficios_sin_solicitud": str(salida_sin_solicitud),
        "salida_resumen_validacion": str(salida_resumen),
    }
    logger.info("Merge completado: %s", resumen)
    return resumen


def filtrar_oficios_para_merge(df_oficios, logger=None):
    """Excluye oficios con revisado=visto o estado INCOMPLETO/PARCIAL."""
    ofi = df_oficios.copy()
    excluir = pd.Series(False, index=ofi.index)

    if "revisado" in ofi.columns:
        revisado = ofi["revisado"].fillna("").astype(str).str.strip().str.lower()
        excluir |= revisado.eq("visto")

    if "tiene_hoja_ruta" in ofi.columns:
        estado = ofi["tiene_hoja_ruta"].fillna("").astype(str).str.strip().str.upper()
        excluir |= estado.isin(ESTADOS_EXCLUIDOS)

    excluidos = int(excluir.sum())
    if logger:
        logger.info(
            "Oficios excluidos del merge (revisado=visto y/o INCOMPLETO/PARCIAL): %s",
            excluidos,
        )
    return ofi.loc[~excluir].copy(), excluidos


def calcular_oficios_sin_solicitud(df_solicitudes, df_oficios):
    """Oficios elegibles cuya hoja_ruta no aparece en ninguna solicitud."""
    sol_keys = {
        k for k in df_solicitudes["hoja_ruta"].map(normalizar_hoja_ruta) if k
    }
    ofi = df_oficios.copy()
    ofi["_key"] = ofi["hoja_ruta"].map(normalizar_hoja_ruta)
    ofi = ofi[ofi["_key"].astype(bool)]
    sin = ofi[~ofi["_key"].isin(sol_keys)].drop(columns=["_key"]).copy()
    return sin


def merge_solicitud_oficio(df_solicitudes, df_oficios, logger=None):
    sol = df_solicitudes.copy()
    ofi = df_oficios.copy()

    sol["_key"] = sol["hoja_ruta"].map(normalizar_hoja_ruta)
    ofi["_key"] = ofi["hoja_ruta"].map(normalizar_hoja_ruta)

    ofi = ofi[ofi["_key"].astype(bool)].copy()
    duplicados = int(ofi["_key"].duplicated().sum())
    if duplicados and logger:
        logger.warning(
            "Oficios con hoja_ruta duplicada (se conserva la primera): %s",
            duplicados,
        )
    ofi = ofi.drop_duplicates(subset=["_key"], keep="first")

    cols_ofi = [c for c in ofi.columns if c not in {"_key", "hoja_ruta"}]
    ofi_merge = ofi[["_key"] + cols_ofi].rename(
        columns={c: f"{c}_oficio" if c in sol.columns else c for c in cols_ofi}
    )

    merged = sol.merge(ofi_merge, on="_key", how="left", indicator=True)
    merged["match_oficio"] = merged["_merge"].eq("both")
    merged = merged.drop(columns=["_key", "_merge"])
    return merged


def cargar_tabla(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    suffix = ruta.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(ruta)

    # Detectar separador por primera linea.
    muestra = ruta.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cabecera = muestra[0] if muestra else ""
    sep = ";" if cabecera.count(";") > cabecera.count(",") else ","
    return pd.read_csv(ruta, sep=sep, encoding="utf-8-sig")


def _resolver_entrada(carpeta, stem, preferidas=(".csv", ".xlsx", ".xls")):
    carpeta = Path(carpeta)
    for ext in preferidas:
        candidato = carpeta / f"{stem}{ext}"
        if candidato.exists():
            return candidato

    existentes = sorted(carpeta.glob(f"{stem}.*"))
    if existentes:
        return existentes[0]

    raise FileNotFoundError(
        f"No se encontro {stem}.* en {carpeta} (extensiones: {', '.join(preferidas)})"
    )
