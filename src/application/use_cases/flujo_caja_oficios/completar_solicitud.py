from pathlib import Path

import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio
from utils.inventarios.limpieza import normalizar_hoja_ruta

ESTADOS_EXCLUIDOS = {"INCOMPLETO", "PARCIAL"}

# Fichas que, segun el informe, NO deben considerarse en el inventario final.
# Se detectaron 5 expedientes que no corresponden y se retiran del conteo final.
FICHAS_EXCLUIDAS_POR_INFORME = [
    "E-008619-2019",
    "E-008621-2019",
    "E-008630-2019",
    "E-008636-2019",
    "E-008657-2019",
]


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

    # Validacion de los 2 contenidos, contada por EXPEDIENTE unico (hoja_ruta).
    # Reportes aparte, no tocan el inventario final.
    sol_keys = {k for k in df_solicitudes["hoja_ruta"].map(normalizar_hoja_ruta) if k}
    ofi_keys = {k for k in oficios_elegibles["hoja_ruta"].map(normalizar_hoja_ruta) if k}
    exp_ambos = sol_keys & ofi_keys            # unidos (misma cifra desde ambas fuentes)
    exp_sol_sin_ofi = sol_keys - ofi_keys      # solicitud sin oficio
    exp_ofi_sin_sol = ofi_keys - sol_keys      # oficio sin solicitud

    # Detalle: solicitudes sin oficio (una fila por expediente; las solicitudes ya son unicas).
    if "match_oficio" in df_result.columns:
        sin_oficio = df_result[~df_result["match_oficio"].astype(bool)].copy()
    else:
        sin_oficio = df_result.iloc[0:0].copy()
    salida_sin_oficio = reportes_oficios / "solicitudes_sin_oficio.csv"
    sin_oficio.to_csv(salida_sin_oficio, index=False, encoding="utf-8-sig")

    # Detalle: oficios sin solicitud (deduplicado por expediente).
    sin_solicitud = calcular_oficios_sin_solicitud(df_solicitudes, oficios_elegibles)
    salida_sin_solicitud = reportes_oficios / "oficios_sin_solicitud.csv"
    sin_solicitud.to_csv(salida_sin_solicitud, index=False, encoding="utf-8-sig")

    con_oficio = int(df_result["match_oficio"].sum()) if "match_oficio" in df_result.columns else 0

    # Descomposicion de las SOLICITUDES (se reparten en 3 grupos que suman el total):
    #   con oficio  +  sin oficio pero armadas en Flujo 1  +  sueltas  =  total
    # Solicitudes SUELTAS: pasaron al Flujo 2 pero quedaron sin resolver
    # (sin oficio Y sin expediente armado en Flujo 1: expediente_final + subsanados).
    armados_keys = _cargar_expedientes_armados(config, logger)
    solicitudes_armadas = sol_keys & armados_keys      # sin oficio pero armadas en F1
    sueltas_keys = sol_keys - ofi_keys - armados_keys  # ni oficio ni armado
    sin_oficio_norm = sin_oficio["hoja_ruta"].map(normalizar_hoja_ruta)
    sueltas = sin_oficio[~sin_oficio_norm.isin(armados_keys)].copy()
    salida_sueltas = reportes_oficios / "solicitudes_sueltas.csv"
    sueltas.to_csv(salida_sueltas, index=False, encoding="utf-8-sig")

    # Inventario final de entrega = expedientes con oficio + expedientes armados,
    # menos las fichas que segun el informe no deben considerarse.
    inventario_keys = exp_ambos | armados_keys
    excluidas_keys = {normalizar_hoja_ruta(c) for c in FICHAS_EXCLUIDAS_POR_INFORME if c}
    excluidas_en_inventario = inventario_keys & excluidas_keys
    inventario_final = len(inventario_keys - excluidas_keys)

    # Resumen de validacion por EXPEDIENTE. La seccion de solicitudes deja claro
    # como se reparte el total (con oficio / armadas / sueltas).
    resumen_validacion = pd.DataFrame(
        [
            # --- Solicitudes que pasaron al Flujo 2 (suman el total) ---
            ("solicitudes_total", len(sol_keys)),
            ("solicitudes_con_oficio", len(exp_ambos)),
            ("solicitudes_sin_oficio_pero_armadas", len(solicitudes_armadas)),
            ("solicitudes_sueltas", len(sueltas_keys)),
            # --- Oficios ---
            ("oficios_elegibles", len(ofi_keys)),
            ("oficios_con_solicitud", len(exp_ambos)),
            ("oficios_sin_solicitud", len(exp_ofi_sin_sol)),
            # --- Expedientes armados en Flujo 1 ---
            ("expedientes_armados_flujo1", len(armados_keys)),
            # --- Inventario final de entrega ---
            ("inventario_final_bruto", len(inventario_keys)),
            ("fichas_excluidas_por_informe", len(excluidas_en_inventario)),
            ("inventario_final", inventario_final),
            # --- Referencia de oficios crudos ---
            ("oficios_entrada_filas", len(df_oficios)),
            ("oficios_excluidos_parcial_incompleto_visto", oficios_excluidos),
            ("oficios_elegibles_filas", len(oficios_elegibles)),
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
        "solicitudes_total": len(sol_keys),
        "solicitudes_con_oficio": len(exp_ambos),
        "solicitudes_sin_oficio_pero_armadas": len(solicitudes_armadas),
        "solicitudes_sueltas": len(sueltas_keys),
        "oficios_elegibles": len(ofi_keys),
        "oficios_con_solicitud": len(exp_ambos),
        "oficios_sin_solicitud": len(exp_ofi_sin_sol),
        "expedientes_armados_flujo1": len(armados_keys),
        "inventario_final_bruto": len(inventario_keys),
        "fichas_excluidas_por_informe": len(excluidas_en_inventario),
        "inventario_final": inventario_final,
        "salida": str(salida),
        "salida_solicitudes_sin_oficio": str(salida_sin_oficio),
        "salida_oficios_sin_solicitud": str(salida_sin_solicitud),
        "salida_solicitudes_sueltas": str(salida_sueltas),
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


def _cargar_expedientes_armados(config, logger=None):
    """Expedientes armados en Flujo 1 (expediente_final + subsanados) por hoja_ruta.

    Se usa solo para el conteo de solicitudes 'sueltas'. Si faltan rutas o
    archivos, se omite sin romper la validacion.
    """
    rf = config.get("paths", {}).get("output", {}).get("resultados_finales", {})
    fuentes = []
    ef = rf.get("expediente_final")
    if ef:
        for nombre in ("inventario_final_alimentado_limpio.csv", "inventario_final.xlsx"):
            candidato = Path(ef) / nombre
            if candidato.exists():
                fuentes.append(candidato)
                break
    sub = rf.get("expediente_subsanados")
    if sub:
        candidato = Path(sub) / "inventario_subsanados.csv"
        if candidato.exists():
            fuentes.append(candidato)

    claves = set()
    for ruta in fuentes:
        try:
            df = cargar_tabla(ruta)
        except Exception:
            if logger:
                logger.warning("No se pudo leer expedientes armados: %s", ruta)
            continue
        if "hoja_ruta" in df.columns:
            claves |= {k for k in df["hoja_ruta"].map(normalizar_hoja_ruta) if k}

    if logger:
        if fuentes:
            logger.info(
                "Expedientes armados Flujo 1 (para 'sueltas'): %s | fuentes: %s",
                len(claves),
                [str(f) for f in fuentes],
            )
        else:
            logger.warning(
                "No se hallaron inventarios de Flujo 1 (expediente_final/subsanados); "
                "'solicitudes_sueltas' contara todas las que no tienen oficio."
            )
    return claves


def calcular_oficios_sin_solicitud(df_solicitudes, df_oficios):
    """Oficios elegibles (por expediente) cuya hoja_ruta no aparece en ninguna solicitud."""
    sol_keys = {
        k for k in df_solicitudes["hoja_ruta"].map(normalizar_hoja_ruta) if k
    }
    ofi = df_oficios.copy()
    ofi["_key"] = ofi["hoja_ruta"].map(normalizar_hoja_ruta)
    ofi = ofi[ofi["_key"].astype(bool)]
    sin = ofi[~ofi["_key"].isin(sol_keys)].copy()
    # Un expediente por fila (conserva la primera aparicion).
    sin = sin.drop_duplicates(subset=["_key"], keep="first").drop(columns=["_key"])
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
