import os
import re
import shutil
import time
from pathlib import Path

import pandas as pd

from infrastructure.ocr.fichas_oficios import (
    PAGINAS_OFICIO,
    PAGINAS_OFICIO_PARCIAL,
    extraer_datos_oficio_pagina,
    quitar_acentos,
)
from infrastructure.ocr.ocr_tools import validar_idiomas_tesseract
from infrastructure.storage.filesystem import asegurar_directorio

COLUMNAS_SALIDA = [
    "empresa",
    "archivo",
    "hoja_ruta",
    "conformidad",
    "tiene_hoja_ruta",
]
COLUMNAS_INTERNAS = COLUMNAS_SALIDA + ["ruta_pdf"]
# No colapsar espacios en nombres de archivo: deben coincidir con el PDF en disco.
COLUMNAS_LITERALES = {"archivo", "ruta_pdf"}

CARPETAS_IGNORADAS = {
    "otros",
    "completo",
    "parcial",
    "incompleto",
    "telefonica_otros",
}
ESTADOS_CLASIFICACION = ("COMPLETO", "PARCIAL", "INCOMPLETO")


def ejecutar(
    config,
    logger,
    empresa=None,
    excluir=None,
    clasificar=True,
    limite=None,
    debug=False,
    solo_tabla=False,
    solo_parcial=False,
):
    """
    OCR de oficios.
    - solo_parcial: 2a pasada sobre carpetas PARCIAL e INCOMPLETO de cada empresa.
      Conserva lista_fichas_oficios existente y solo actualiza si hay dato nuevo.
    """
    if solo_parcial and solo_tabla:
        raise ValueError("No se puede usar --solo-parcial junto con --solo-tabla.")

    config_ocr = dict(config.get("ocr", {}))
    validacion_ocr = validar_idiomas_tesseract(config_ocr, logger=logger)

    rutas = config["paths"]
    entrada = Path(rutas["input"]["oficios"])
    reportes = Path(rutas["work"]["fichas_oficios"]["reportes"])

    asegurar_directorio(reportes)

    if solo_parcial:
        return _ejecutar_segunda_pasada_parcial(
            config=config,
            logger=logger,
            entrada=entrada,
            reportes=reportes,
            config_ocr=config_ocr,
            validacion_ocr=validacion_ocr,
            empresa=empresa,
            excluir=excluir,
            clasificar=clasificar,
            limite=limite,
            debug=debug,
            paginas=PAGINAS_OFICIO_PARCIAL,
        )

    paginas = PAGINAS_OFICIO

    incluir_clasificados = solo_tabla or not clasificar

    empresas = descubrir_empresas(
        entrada,
        filtro=empresa,
        excluir=excluir,
        incluir_clasificados=incluir_clasificados,
    )
    if not empresas:
        raise FileNotFoundError(
            f"No se encontraron carpetas de empresa en {entrada}"
            + (f" para filtro '{empresa}'." if empresa else ".")
        )

    logger.info(
        "Fichas oficios iniciado. Empresas=%s | paginas=%s | solo_tabla=%s",
        len(empresas),
        paginas,
        solo_tabla,
    )

    tiempo_inicio = time.perf_counter()
    registros = []
    resumen_empresas = []

    for nombre_empresa in empresas:
        pdfs = listar_pdfs_empresa(
            entrada,
            nombre_empresa,
            limite=limite,
            incluir_clasificados=incluir_clasificados,
        )
        if not pdfs:
            logger.info("Empresa sin PDF: %s", nombre_empresa)
            continue

        logger.info("Procesando empresa %s (%s PDF)", nombre_empresa, len(pdfs))
        df_empresa = procesar_oficios_empresa(
            pdfs=pdfs,
            empresa=nombre_empresa,
            paginas=paginas,
            config_ocr=config_ocr,
            debug=debug,
            logger=logger,
        )

        carpeta_empresa = entrada / nombre_empresa
        csv_empresa = carpeta_empresa / "lista_fichas_oficios.csv"
        # En corrida parcial (solo PDF sueltos) se hace upsert por archivo.
        # Con --solo-tabla se regenera la empresa completa desde carpetas.
        _guardar_tabla_empresa(
            df_empresa,
            csv_empresa,
            reemplazar=bool(solo_tabla),
        )

        if clasificar and not solo_tabla:
            distribuidos = distribuir_pdfs_empresa(entrada, df_empresa, logger)
        else:
            distribuidos = 0

        registros.extend(_tabla_publica(df_empresa).to_dict(orient="records"))
        resumen_empresas.append(
            {
                "empresa": nombre_empresa,
                "pdfs_procesados": len(df_empresa),
                "completo": int((df_empresa["tiene_hoja_ruta"] == "COMPLETO").sum()),
                "parcial": int((df_empresa["tiene_hoja_ruta"] == "PARCIAL").sum()),
                "incompleto": int((df_empresa["tiene_hoja_ruta"] == "INCOMPLETO").sum()),
                "pdfs_distribuidos": distribuidos,
                "csv": str(csv_empresa),
            }
        )

    df_nuevos = pd.DataFrame(registros)
    if df_nuevos.empty:
        df_nuevos = pd.DataFrame(columns=COLUMNAS_SALIDA)

    csv_general = reportes / "lista_fichas_oficios.csv"
    empresas_actualizadas = [r["empresa"] for r in resumen_empresas]
    df_total = actualizar_tabla_acumulada(
        csv_general,
        df_nuevos,
        empresas_actualizadas=empresas_actualizadas,
        reemplazar_empresa=bool(solo_tabla),
        logger=logger,
    )
    # Alinea estado/nombre con carpetas reales (evita desfase COMPLETO vs disco).
    if empresas_actualizadas:
        df_total, sync_info = sincronizar_empresas_desde_disco(
            entrada,
            df_total,
            empresas=empresas_actualizadas,
            logger=logger,
        )
        for item in resumen_empresas:
            info = sync_info.get(item["empresa"])
            if info:
                item["pdfs_en_disco"] = info["total"]
                item["completo"] = info["completo"]
                item["parcial"] = info["parcial"]
                item["incompleto"] = info["incompleto"]

    _guardar_dataframe_extendido(df_total, csv_general)
    for nombre_empresa in empresas_actualizadas:
        carpeta_empresa = entrada / nombre_empresa
        csv_empresa = carpeta_empresa / "lista_fichas_oficios.csv"
        sub = df_total[df_total["empresa"].astype(str) == str(nombre_empresa)]
        if not sub.empty:
            _guardar_tabla_empresa(sub, csv_empresa, reemplazar=True)

    # El resumen debe reflejar el total por empresa en la tabla general.
    resumen_empresas = _resumen_desde_tabla(df_total, resumen_empresas)

    csv_resumen = reportes / "resumen_fichas_oficios_por_empresa.csv"
    df_resumen = actualizar_resumen_empresas(
        csv_resumen,
        pd.DataFrame(resumen_empresas),
        logger=logger,
    )
    df_resumen.to_csv(csv_resumen, index=False, encoding="utf-8-sig")

    tiempo_total = time.perf_counter() - tiempo_inicio
    return {
        "proceso": "05_ocr_fichas_oficios",
        "estado": "completado",
        "entrada": str(entrada),
        "empresa_filtro": empresa or "todas",
        "empresas_procesadas": len(resumen_empresas),
        "empresas_en_tabla_general": int(df_total["empresa"].nunique()) if not df_total.empty else 0,
        "pdfs_procesados": len(df_nuevos),
        "pdfs_en_tabla_general": len(df_total),
        "completo": int((df_nuevos.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "COMPLETO").sum()),
        "parcial": int((df_nuevos.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "PARCIAL").sum()),
        "incompleto": int((df_nuevos.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "INCOMPLETO").sum()),
        "csv_general": str(csv_general),
        "tiempo_total_seg": round(tiempo_total, 2),
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def descubrir_empresas(entrada, filtro=None, excluir=None, incluir_clasificados=False):
    entrada = Path(entrada)
    if not entrada.exists():
        return []

    filtro_norm = _normalizar_nombre(filtro) if filtro else ""
    excluir_norm = {_normalizar_nombre(x) for x in (excluir or []) if str(x).strip()}
    empresas = []

    for carpeta in sorted(entrada.iterdir()):
        if not carpeta.is_dir():
            continue
        if carpeta.name.lower() in CARPETAS_IGNORADAS:
            continue
        nombre_norm = _normalizar_nombre(carpeta.name)
        if excluir_norm and nombre_norm in excluir_norm:
            continue
        if filtro_norm and filtro_norm not in nombre_norm:
            continue
        if _empresa_tiene_pdfs(carpeta, incluir_clasificados=incluir_clasificados):
            empresas.append(carpeta.name)

    return empresas


def _empresa_tiene_pdfs(carpeta, incluir_clasificados=False):
    for nombre in os.listdir(carpeta):
        if nombre.lower().endswith(".pdf"):
            return True
    if not incluir_clasificados:
        return False
    for estado in ESTADOS_CLASIFICACION:
        sub = carpeta / estado
        if sub.is_dir() and any(p.suffix.lower() == ".pdf" for p in sub.iterdir()):
            return True
    return False


def listar_pdfs_empresa(entrada, empresa, limite=None, incluir_clasificados=False):
    base = Path(entrada) / empresa
    if not base.exists():
        return []

    pdfs = sorted(pdf for pdf in base.glob("*.pdf") if pdf.is_file())
    if incluir_clasificados:
        for estado in ESTADOS_CLASIFICACION:
            sub = base / estado
            if sub.is_dir():
                pdfs.extend(sorted(pdf for pdf in sub.glob("*.pdf") if pdf.is_file()))
        pdfs = sorted(pdfs, key=lambda p: p.name.lower())

    if limite:
        pdfs = pdfs[:limite]
    return pdfs


def listar_pdfs_reproceso(entrada, empresa, limite=None, estados=("PARCIAL", "INCOMPLETO")):
    """PDF en carpetas de reproceso (PARCIAL e INCOMPLETO por defecto)."""
    base = Path(entrada) / empresa
    if not base.exists():
        return []
    pdfs = []
    for estado in estados:
        sub = base / estado
        if sub.is_dir():
            pdfs.extend(sorted(pdf for pdf in sub.glob("*.pdf") if pdf.is_file()))
    pdfs = sorted(pdfs, key=lambda p: (p.parent.name, p.name.lower()))
    if limite:
        pdfs = pdfs[:limite]
    return pdfs


def listar_pdfs_parcial(entrada, empresa, limite=None):
    """Compatibilidad: PARCIAL + INCOMPLETO."""
    return listar_pdfs_reproceso(entrada, empresa, limite=limite)


def _consolidar_paginas_oficio(resultados_pagina):
    """Une OCR de varias páginas en un solo dict (1 fila por PDF)."""
    hoja = ""
    conf = ""
    archivo = ""
    for datos in resultados_pagina:
        archivo = archivo or str(datos.get("archivo") or "")
        hr = str(datos.get("hoja_ruta") or "").strip()
        cf = str(datos.get("conformidad") or "").strip()
        if hr and not hoja:
            hoja = hr
        if cf == "CUENTA":
            conf = "CUENTA"
        elif cf and not conf:
            conf = cf
    return {"hoja_ruta": hoja, "conformidad": conf, "archivo": archivo}


def procesar_oficios_empresa(
    pdfs,
    empresa,
    paginas,
    config_ocr,
    debug=False,
    logger=None,
):
    """Una fila por PDF: consolida OCR de todas las páginas configuradas."""
    filas = []
    for pdf_path in pdfs:
        por_pagina = []
        for pagina in paginas:
            try:
                datos = extraer_datos_oficio_pagina(
                    pdf_path=pdf_path,
                    pagina_1based=pagina,
                    config_ocr=config_ocr,
                    mostrar_debug=debug,
                )
            except Exception as exc:
                if logger:
                    logger.exception("Error OCR fichas oficios: %s p.%s", pdf_path, pagina)
                datos = {
                    "hoja_ruta": "",
                    "conformidad": "",
                    "archivo": pdf_path.name,
                    "error": str(exc),
                }
            por_pagina.append(datos)

        datos = _consolidar_paginas_oficio(por_pagina)
        datos["archivo"] = pdf_path.name
        datos["empresa"] = empresa
        datos["ruta_pdf"] = str(pdf_path)
        datos["tiene_hoja_ruta"] = clasificar_estado_oficio(datos)
        filas.append(datos)

    df = pd.DataFrame(filas)
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS_INTERNAS)
    return df.reindex(columns=COLUMNAS_INTERNAS)


def _texto_celda(valor):
    """Normaliza NaN/None a cadena vacía (pandas convierte celdas vacías a NaN)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    if texto.lower() in {"nan", "none", "nat"}:
        return ""
    return texto


def _es_hoja_ruta_util(valor):
    txt = _texto_celda(valor)
    if not txt or len(txt) > 30:
        return False
    return "REFERENCIA" not in txt.upper()


def _fusionar_dato_conservador(prev_val, nuevo_val, tipo="texto"):
    """
    Conserva ediciones previas: solo rellena vacío o mejora conformidad a CUENTA.
    No sobrescribe hoja_ruta/texto ya llenos.
    """
    prev = _texto_celda(prev_val)
    nuevo = _texto_celda(nuevo_val)
    if not nuevo:
        return prev, False
    if tipo == "hoja_ruta":
        if not _es_hoja_ruta_util(nuevo):
            return prev, False
        if not _es_hoja_ruta_util(prev):
            return nuevo, True
        return prev, False
    if tipo == "conformidad":
        if prev != "CUENTA" and nuevo == "CUENTA":
            return nuevo, True
        if not prev and nuevo:
            return nuevo, True
        return prev, False
    if not prev:
        return nuevo, True
    return prev, False


def aplicar_ocr_conservador_sobre_fila(fila_previa, fila_ocr):
    """
    Combina OCR nuevo sobre fila existente sin pisar cambios manuales.
    Retorna (fila_dict, hubo_cambio_dato).
    """
    out = dict(fila_previa) if fila_previa else {}
    for col in COLUMNAS_SALIDA + ["revisado", "ruta_pdf"]:
        if col not in out:
            out[col] = ""

    hubo = False
    hoja, c1 = _fusionar_dato_conservador(
        out.get("hoja_ruta"), fila_ocr.get("hoja_ruta"), tipo="hoja_ruta"
    )
    conf, c2 = _fusionar_dato_conservador(
        out.get("conformidad"), fila_ocr.get("conformidad"), tipo="conformidad"
    )
    if c1:
        out["hoja_ruta"] = hoja
        hubo = True
    if c2:
        out["conformidad"] = conf
        hubo = True

    estado_nuevo = clasificar_estado_oficio(out)
    estado_prev = str(out.get("tiene_hoja_ruta") or "").strip()
    if estado_nuevo != estado_prev:
        out["tiene_hoja_ruta"] = estado_nuevo
        hubo = True
    else:
        out["tiene_hoja_ruta"] = estado_prev or estado_nuevo

    if fila_ocr.get("ruta_pdf"):
        out["ruta_pdf"] = fila_ocr["ruta_pdf"]
    if fila_ocr.get("empresa"):
        out["empresa"] = fila_ocr["empresa"]
    if fila_ocr.get("archivo"):
        # Conserva nombre previo si ya existía (ediciones de nombre en CSV).
        if not str(out.get("archivo") or "").strip():
            out["archivo"] = fila_ocr["archivo"]
    return out, hubo


def _ejecutar_segunda_pasada_parcial(
    config,
    logger,
    entrada,
    reportes,
    config_ocr,
    validacion_ocr,
    empresa=None,
    excluir=None,
    clasificar=True,
    limite=None,
    debug=False,
    paginas=None,
):
    """
    2a pasada: OCR páginas configuradas sobre PARCIAL e INCOMPLETO.
    No regenera la tabla completa: actualiza fila a fila solo si hay dato nuevo.
    """
    paginas = paginas or PAGINAS_OFICIO
    estados_reproceso = ("PARCIAL", "INCOMPLETO")
    csv_general = reportes / "lista_fichas_oficios.csv"
    df_total = _cargar_tabla_existente(csv_general) if csv_general.exists() else pd.DataFrame()
    if df_total.empty:
        raise FileNotFoundError(
            f"No existe tabla previa para conservar: {csv_general}. "
            "La 2a pasada requiere lista_fichas_oficios ya editada."
        )

    for col in COLUMNAS_SALIDA + ["revisado"]:
        if col not in df_total.columns:
            df_total[col] = ""

    empresas = descubrir_empresas(
        entrada,
        filtro=empresa,
        excluir=excluir,
        incluir_clasificados=True,
    )
    empresas = [e for e in empresas if e.lower() not in CARPETAS_IGNORADAS]
    if not empresas:
        raise FileNotFoundError(
            f"No se encontraron empresas con PARCIAL/INCOMPLETO en {entrada}"
            + (f" para filtro '{empresa}'." if empresa else ".")
        )

    logger.info(
        "2a pasada PARCIAL+INCOMPLETO. Empresas=%s | excluidas=%s | paginas=%s | clasificar=%s | csv=%s",
        len(empresas),
        len(excluir or []),
        paginas,
        clasificar,
        csv_general,
    )
    if excluir:
        logger.info("Empresas excluidas: %s", ", ".join(excluir))
    logger.info("Empresas a procesar: %s", ", ".join(empresas))

    tiempo_inicio = time.perf_counter()
    resumen_empresas = []
    total_pdfs = 0
    total_actualizados = 0
    total_distribuidos = 0

    def _indice_por_clave(df):
        return {
            k: label
            for label, k in zip(df.index.tolist(), _clave_empresa_archivo(df).tolist())
        }

    indice = _indice_por_clave(df_total)

    for nombre_empresa in empresas:
        pdfs = listar_pdfs_reproceso(
            entrada,
            nombre_empresa,
            limite=limite,
            estados=estados_reproceso,
        )
        if not pdfs:
            logger.info("Empresa sin PDF en PARCIAL/INCOMPLETO: %s", nombre_empresa)
            continue

        n_parcial = sum(1 for p in pdfs if p.parent.name.upper() == "PARCIAL")
        n_incompleto = sum(1 for p in pdfs if p.parent.name.upper() == "INCOMPLETO")
        logger.info(
            "2a pasada empresa %s (%s PDF: PARCIAL=%s INCOMPLETO=%s) paginas=%s",
            nombre_empresa,
            len(pdfs),
            n_parcial,
            n_incompleto,
            paginas,
        )
        df_ocr = procesar_oficios_empresa(
            pdfs=pdfs,
            empresa=nombre_empresa,
            paginas=paginas,
            config_ocr=config_ocr,
            debug=debug,
            logger=logger,
        )
        total_pdfs += len(df_ocr)

        actualizados_emp = 0
        filas_mover = []
        for _, fila_ocr in df_ocr.iterrows():
            clave = (
                f"{str(fila_ocr['empresa']).strip()}||"
                f"{_normalizar_espacios_archivo(fila_ocr['archivo'])}"
            )
            label = indice.get(clave)
            estado_carpeta = Path(str(fila_ocr.get("ruta_pdf") or "")).parent.name.upper()
            if estado_carpeta not in ESTADOS_CLASIFICACION:
                estado_carpeta = "INCOMPLETO"
            previa = df_total.loc[label].to_dict() if label is not None else {
                "empresa": nombre_empresa,
                "archivo": fila_ocr["archivo"],
                "hoja_ruta": "",
                "conformidad": "",
                "tiene_hoja_ruta": estado_carpeta,
                "revisado": "",
            }
            fusion, hubo = aplicar_ocr_conservador_sobre_fila(previa, fila_ocr.to_dict())
            if not hubo:
                continue

            actualizados_emp += 1
            total_actualizados += 1
            if label is not None:
                for col, val in fusion.items():
                    df_total.loc[label, col] = val
            else:
                df_total = pd.concat([df_total, pd.DataFrame([fusion])], ignore_index=True)
                indice = _indice_por_clave(df_total)

            if clasificar and fusion.get("tiene_hoja_ruta") in ESTADOS_CLASIFICACION:
                filas_mover.append(
                    {
                        "empresa": fusion["empresa"],
                        "archivo": fusion.get("archivo") or fila_ocr["archivo"],
                        "tiene_hoja_ruta": fusion["tiene_hoja_ruta"],
                        "ruta_pdf": fila_ocr["ruta_pdf"],
                    }
                )

        distribuidos = 0
        if clasificar and filas_mover:
            distribuidos = distribuir_pdfs_empresa(
                entrada, pd.DataFrame(filas_mover), logger
            )
            total_distribuidos += distribuidos

        resumen_empresas.append(
            {
                "empresa": nombre_empresa,
                "pdfs_procesados": len(df_ocr),
                "filas_actualizadas": actualizados_emp,
                "completo": int(
                    (
                        df_total[df_total["empresa"].astype(str) == str(nombre_empresa)][
                            "tiene_hoja_ruta"
                        ]
                        == "COMPLETO"
                    ).sum()
                ),
                "parcial": int(
                    (
                        df_total[df_total["empresa"].astype(str) == str(nombre_empresa)][
                            "tiene_hoja_ruta"
                        ]
                        == "PARCIAL"
                    ).sum()
                ),
                "incompleto": int(
                    (
                        df_total[df_total["empresa"].astype(str) == str(nombre_empresa)][
                            "tiene_hoja_ruta"
                        ]
                        == "INCOMPLETO"
                    ).sum()
                ),
                "pdfs_distribuidos": distribuidos,
                "csv": str(entrada / nombre_empresa / "lista_fichas_oficios.csv"),
            }
        )
        logger.info(
            "Empresa %s: OCR=%s | actualizadas=%s | movidos=%s",
            nombre_empresa,
            len(df_ocr),
            actualizados_emp,
            distribuidos,
        )

    # Guarda general preservando columnas extra y separador original.
    _guardar_dataframe_extendido(df_total, csv_general)

    empresas_tocadas = [r["empresa"] for r in resumen_empresas]
    cols_pub = [c for c in COLUMNAS_SALIDA + ["revisado"] if c in df_total.columns]
    for nombre_empresa in empresas_tocadas:
        sub = df_total[df_total["empresa"].astype(str) == str(nombre_empresa)][cols_pub]
        csv_empresa = entrada / nombre_empresa / "lista_fichas_oficios.csv"
        try:
            # Escribe desde la tabla general (incluye ediciones del usuario).
            _guardar_dataframe_extendido(sub, csv_empresa)
        except PermissionError:
            logger.warning("CSV empresa abierto (Excel?): %s", csv_empresa)

    csv_resumen = reportes / "resumen_fichas_oficios_por_empresa.csv"
    df_resumen = actualizar_resumen_empresas(
        csv_resumen,
        pd.DataFrame(resumen_empresas),
        logger=logger,
    )
    if not df_resumen.empty:
        df_resumen.to_csv(csv_resumen, index=False, encoding="utf-8-sig")

    tiempo_total = time.perf_counter() - tiempo_inicio
    return {
        "proceso": "05_ocr_fichas_oficios_segunda_pasada_parcial",
        "estado": "completado",
        "entrada": str(entrada),
        "empresa_filtro": empresa or "todas",
        "empresas_procesadas": len(resumen_empresas),
        "pdfs_procesados": total_pdfs,
        "filas_actualizadas": total_actualizados,
        "pdfs_distribuidos": total_distribuidos,
        "pdfs_en_tabla_general": len(df_total),
        "paginas_ocr": paginas,
        "csv_general": str(csv_general),
        "modo": "conservador (no pisa hoja_ruta/revisado ya llenos)",
        "tiempo_total_seg": round(tiempo_total, 2),
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def _tabla_publica(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNAS_SALIDA)
    return df.reindex(columns=COLUMNAS_SALIDA)


def _detectar_separador_csv(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return ","
    muestra = ruta.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cabecera = muestra[0] if muestra else ""
    return ";" if cabecera.count(";") > cabecera.count(",") else ","


def _cargar_tabla_existente(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return pd.DataFrame()

    if ruta.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(ruta)
    else:
        sep = _detectar_separador_csv(ruta)
        df = pd.read_csv(ruta, sep=sep, encoding="utf-8-sig")
    return df.fillna("")


def _preservar_revisado(df_nuevo, df_previo):
    """Conserva columna revisado previa al regenerar OCR de la misma empresa."""
    tabla = _tabla_publica(df_nuevo).copy()
    if df_previo is None or df_previo.empty or "archivo" not in df_previo.columns:
        if "revisado" not in tabla.columns:
            tabla["revisado"] = ""
        return tabla

    previo = df_previo.copy()
    if "revisado" not in previo.columns:
        if "revisado" not in tabla.columns:
            tabla["revisado"] = ""
        return tabla

    mapa = {
        str(row["archivo"]).strip(): str(row.get("revisado") or "").strip()
        for _, row in previo.iterrows()
        if str(row.get("archivo") or "").strip()
    }
    tabla["revisado"] = tabla["archivo"].map(lambda a: mapa.get(str(a).strip(), ""))
    return tabla


def _guardar_tabla_empresa(df, destino, reemplazar=False):
    destino = Path(destino)
    asegurar_directorio(destino.parent)
    nuevos = _tabla_publica(df).copy()
    if reemplazar or not destino.exists() or nuevos.empty:
        previo = _cargar_tabla_existente(destino) if destino.exists() else pd.DataFrame()
        _guardar_dataframe_extendido(_preservar_revisado(nuevos, previo), destino)
        return

    previo = _cargar_tabla_existente(destino)
    if "archivo" not in previo.columns:
        _guardar_dataframe_extendido(_preservar_revisado(nuevos, previo), destino)
        return

    nuevos = _preservar_revisado(nuevos, previo)
    claves = set(nuevos["archivo"].astype(str).str.strip())
    resto = previo[~previo["archivo"].astype(str).str.strip().isin(claves)].copy()
    for col in COLUMNAS_SALIDA + ["revisado"]:
        if col not in resto.columns:
            resto[col] = ""
        if col not in nuevos.columns:
            nuevos[col] = ""
    cols = [c for c in COLUMNAS_SALIDA + ["revisado"] if c in resto.columns or c in nuevos.columns]
    _guardar_dataframe_extendido(
        pd.concat([resto[cols], nuevos[cols]], ignore_index=True),
        destino,
    )


def _normalizar_espacios_archivo(texto):
    return re.sub(r"\s+", " ", str(texto or "").strip()).lower()


def _clave_empresa_archivo(df):
    """Clave tolerante a espacios múltiples en el nombre de archivo."""
    return (
        df["empresa"].astype(str).str.strip()
        + "||"
        + df["archivo"].map(_normalizar_espacios_archivo)
    )


def _resumen_desde_tabla(df_total, resumen_empresas):
    if not resumen_empresas:
        return resumen_empresas
    if df_total is None or df_total.empty or "empresa" not in df_total.columns:
        return resumen_empresas

    out = []
    for item in resumen_empresas:
        emp = item["empresa"]
        sub = df_total[df_total["empresa"].astype(str) == str(emp)]
        actualizado = dict(item)
        actualizado["pdfs_en_tabla_empresa"] = int(len(sub))
        actualizado["completo"] = int(
            (sub.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "COMPLETO").sum()
        )
        actualizado["parcial"] = int(
            (sub.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "PARCIAL").sum()
        )
        actualizado["incompleto"] = int(
            (sub.get("tiene_hoja_ruta", pd.Series(dtype=str)) == "INCOMPLETO").sum()
        )
        out.append(actualizado)
    return out


def sincronizar_empresa_desde_disco(entrada, empresa, df_previo=None, mapa_hoja=None):
    """
    Reconstruye filas de una empresa según PDF en COMPLETO|PARCIAL|INCOMPLETO|raiz.
    Conserva hoja_ruta/conformidad/revisado emparejando por nombre (espacios normalizados).
    """
    entrada = Path(entrada)
    base = entrada / empresa
    if not base.exists():
        return pd.DataFrame(columns=COLUMNAS_SALIDA + ["revisado"]), {
            "empresa": empresa,
            "total": 0,
            "completo": 0,
            "parcial": 0,
            "incompleto": 0,
            "root": 0,
        }

    previo = df_previo.copy() if df_previo is not None else pd.DataFrame()
    if not previo.empty and "empresa" in previo.columns:
        previo = previo[previo["empresa"].astype(str) == str(empresa)].copy()

    por_norm = {}
    if not previo.empty and "archivo" in previo.columns:
        for _, row in previo.iterrows():
            por_norm[_normalizar_espacios_archivo(row["archivo"])] = row.to_dict()

    mapa_hoja = mapa_hoja or {}
    filas = []
    vistos = set()
    conteo = {"COMPLETO": 0, "PARCIAL": 0, "INCOMPLETO": 0, "ROOT": 0}

    for estado in ESTADOS_CLASIFICACION:
        carpeta = base / estado
        if not carpeta.is_dir():
            continue
        for pdf in sorted(carpeta.glob("*.pdf")):
            key = _normalizar_espacios_archivo(pdf.name)
            if key in vistos:
                continue
            vistos.add(key)
            prev = por_norm.get(key)
            hoja = str(prev.get("hoja_ruta") or "").strip() if prev else ""
            conf = str(prev.get("conformidad") or "").strip() if prev else ""
            rev = str(prev.get("revisado") or "").strip() if prev else ""
            if not hoja:
                hoja = mapa_hoja.get(key, "")
            filas.append(
                {
                    "empresa": empresa,
                    "archivo": pdf.name,
                    "hoja_ruta": hoja,
                    "conformidad": conf,
                    "tiene_hoja_ruta": estado,
                    "revisado": rev,
                }
            )
            conteo[estado] += 1

    for pdf in sorted(base.glob("*.pdf")):
        key = _normalizar_espacios_archivo(pdf.name)
        if key in vistos:
            continue
        vistos.add(key)
        prev = por_norm.get(key)
        hoja = str(prev.get("hoja_ruta") or "").strip() if prev else ""
        conf = str(prev.get("conformidad") or "").strip() if prev else ""
        rev = str(prev.get("revisado") or "").strip() if prev else ""
        if not hoja:
            hoja = mapa_hoja.get(key, "")
        estado_prev = str(prev.get("tiene_hoja_ruta") or "").strip() if prev else ""
        # PDF en raiz: no inventar estado de carpeta; queda pendiente de clasificar.
        estado_final = ""
        if estado_prev and estado_prev not in ESTADOS_CLASIFICACION:
            estado_final = estado_prev
        filas.append(
            {
                "empresa": empresa,
                "archivo": pdf.name,
                "hoja_ruta": hoja,
                "conformidad": conf,
                "tiene_hoja_ruta": estado_final,
                "revisado": rev,
            }
        )
        conteo["ROOT"] += 1

    out = pd.DataFrame(filas)
    for col in COLUMNAS_SALIDA + ["revisado"]:
        if col not in out.columns:
            out[col] = ""
    if not out.empty:
        out = out[COLUMNAS_SALIDA + ["revisado"]]
    else:
        out = pd.DataFrame(columns=COLUMNAS_SALIDA + ["revisado"])

    info = {
        "empresa": empresa,
        "total": int(len(out)),
        "completo": conteo["COMPLETO"],
        "parcial": conteo["PARCIAL"],
        "incompleto": conteo["INCOMPLETO"],
        "root": conteo["ROOT"],
    }
    return out, info


def sincronizar_empresas_desde_disco(entrada, df_total, empresas=None, mapa_hoja=None, logger=None):
    """Sustituye en df_total las filas de cada empresa por el inventario real en disco."""
    entrada = Path(entrada)
    if df_total is None or df_total.empty:
        df_total = pd.DataFrame(columns=COLUMNAS_SALIDA + ["revisado"])

    if empresas is None:
        empresas = descubrir_empresas(entrada, incluir_clasificados=True)
    else:
        empresas = list(empresas)

    sync_info = {}
    resto = df_total[~df_total["empresa"].astype(str).isin([str(e) for e in empresas])].copy()
    bloques = [resto] if not resto.empty else []

    for empresa in empresas:
        if str(empresa).lower() in CARPETAS_IGNORADAS:
            continue
        recon, info = sincronizar_empresa_desde_disco(
            entrada,
            empresa,
            df_previo=df_total,
            mapa_hoja=mapa_hoja,
        )
        sync_info[empresa] = info
        if not recon.empty:
            bloques.append(recon)
        if logger:
            logger.info(
                "Sync disco %s: total=%s completo=%s parcial=%s incompleto=%s root=%s",
                empresa,
                info["total"],
                info["completo"],
                info["parcial"],
                info["incompleto"],
                info["root"],
            )

    if not bloques:
        resultado = pd.DataFrame(columns=COLUMNAS_SALIDA + ["revisado"])
    else:
        resultado = pd.concat(bloques, ignore_index=True)
        for col in COLUMNAS_SALIDA + ["revisado"]:
            if col not in resultado.columns:
                resultado[col] = ""
        resultado = resultado[COLUMNAS_SALIDA + ["revisado"]]

    return resultado, sync_info


def ejecutar_sincronizar_disco(config, logger, empresa=None):
    """Solo sincroniza CSV con carpetas COMPLETO|PARCIAL|INCOMPLETO (sin OCR)."""
    rutas = config["paths"]
    entrada = Path(rutas["input"]["oficios"])
    reportes = Path(rutas["work"]["fichas_oficios"]["reportes"])
    asegurar_directorio(reportes)
    csv_general = reportes / "lista_fichas_oficios.csv"

    empresas = descubrir_empresas(
        entrada,
        filtro=empresa,
        incluir_clasificados=True,
    )
    # Excluir 'otros' y nombres de estado aunque existan.
    empresas = [e for e in empresas if e.lower() not in CARPETAS_IGNORADAS]
    if not empresas:
        raise FileNotFoundError("No hay empresas para sincronizar.")

    df_prev = _cargar_tabla_existente(csv_general) if csv_general.exists() else pd.DataFrame()

    mapa_hoja = {}
    rep_cfg = rutas.get("output", {}).get("resultados_finales", {}).get("expedientes_oficio")
    if rep_cfg:
        rep_csv = Path(rep_cfg) / "reporte_solicitud_oficio.csv"
        if rep_csv.exists():
            rep = pd.read_csv(rep_csv, dtype=str).fillna("")
            if "archivo_oficio" in rep.columns and "hoja_ruta" in rep.columns:
                for a, h in zip(rep["archivo_oficio"], rep["hoja_ruta"]):
                    if str(a).strip() and str(h).strip():
                        mapa_hoja[_normalizar_espacios_archivo(a)] = str(h).strip()

    logger.info("Sincronizando %s empresas desde disco (excluye otros)", len(empresas))
    df_total, sync_info = sincronizar_empresas_desde_disco(
        entrada,
        df_prev,
        empresas=empresas,
        mapa_hoja=mapa_hoja,
        logger=logger,
    )
    _guardar_dataframe_extendido(df_total, csv_general)

    resumen_filas = []
    for nombre in empresas:
        info = sync_info.get(nombre, {})
        sub = df_total[df_total["empresa"].astype(str) == str(nombre)]
        csv_emp = entrada / nombre / "lista_fichas_oficios.csv"
        try:
            _guardar_tabla_empresa(sub, csv_emp, reemplazar=True)
        except PermissionError:
            logger.warning("No se pudo escribir CSV empresa (¿abierto en Excel?): %s", csv_emp)
        resumen_filas.append(
            {
                "empresa": nombre,
                "pdfs_procesados": info.get("total", len(sub)),
                "pdfs_en_tabla_empresa": len(sub),
                "completo": info.get("completo", 0),
                "parcial": info.get("parcial", 0),
                "incompleto": info.get("incompleto", 0),
                "root": info.get("root", 0),
                "pdfs_distribuidos": 0,
                "csv": str(csv_emp),
            }
        )

    csv_resumen = reportes / "resumen_fichas_oficios_por_empresa.csv"
    df_resumen = actualizar_resumen_empresas(csv_resumen, pd.DataFrame(resumen_filas), logger=logger)
    df_resumen.to_csv(csv_resumen, index=False, encoding="utf-8-sig")

    desfasadas = []
    for nombre in empresas:
        info = sync_info.get(nombre, {})
        base = entrada / nombre
        for estado, key in (
            ("COMPLETO", "completo"),
            ("PARCIAL", "parcial"),
            ("INCOMPLETO", "incompleto"),
        ):
            n_disk = len(list((base / estado).glob("*.pdf"))) if (base / estado).exists() else 0
            n_tab = int(info.get(key, 0))
            if n_disk != n_tab:
                desfasadas.append(f"{nombre}/{estado}: disco={n_disk} tabla={n_tab}")

    return {
        "proceso": "sincronizar_oficios_disco",
        "estado": "completado",
        "empresas": len(empresas),
        "filas_tabla": len(df_total),
        "desfases_restantes": len(desfasadas),
        "csv_general": str(csv_general),
        "detalle_desfases": desfasadas[:20],
    }


def actualizar_tabla_acumulada(
    csv_general,
    df_nuevos,
    empresas_actualizadas,
    reemplazar_empresa=False,
    logger=None,
):
    """
    Acumula la tabla general.
    - reemplazar_empresa=True (--solo-tabla): sustituye todas las filas de la empresa.
    - reemplazar_empresa=False: upsert por (empresa, archivo); no borra el resto.
    """
    csv_general = Path(csv_general)
    nuevos = _tabla_publica(df_nuevos).copy()
    previo = _cargar_tabla_existente(csv_general) if csv_general.exists() else pd.DataFrame()

    if previo.empty:
        resultado = nuevos.copy()
        if "revisado" not in resultado.columns:
            resultado["revisado"] = ""
        if logger:
            logger.info("Tabla general nueva: %s filas", len(resultado))
        return resultado

    if not nuevos.empty:
        previo_emp = (
            previo[previo["empresa"].isin(empresas_actualizadas)]
            if "empresa" in previo.columns
            else pd.DataFrame()
        )
        nuevos = _preservar_revisado(nuevos, previo_emp)

    if reemplazar_empresa:
        resto = (
            previo[~previo["empresa"].isin(empresas_actualizadas)].copy()
            if "empresa" in previo.columns
            else pd.DataFrame()
        )
    elif nuevos.empty:
        resto = previo.copy()
    else:
        claves = set(_clave_empresa_archivo(nuevos))
        resto = previo[~_clave_empresa_archivo(previo).isin(claves)].copy()

    resultado = pd.concat([resto, nuevos], ignore_index=True)

    for col in COLUMNAS_SALIDA + ["revisado"]:
        if col not in resultado.columns:
            resultado[col] = ""
    resultado = resultado[COLUMNAS_SALIDA + ["revisado"]]

    if logger:
        logger.info(
            "Tabla general acumulada: previas=%s | actualizadas=%s | total=%s | reemplazar_empresa=%s",
            len(resto),
            len(nuevos),
            len(resultado),
            reemplazar_empresa,
        )
    return resultado


def actualizar_resumen_empresas(csv_resumen, df_nuevos, logger=None):
    csv_resumen = Path(csv_resumen)
    nuevos = df_nuevos.copy()
    if not csv_resumen.exists() or nuevos.empty:
        return nuevos

    previo = pd.read_csv(csv_resumen, encoding="utf-8-sig")
    if "empresa" not in previo.columns:
        return nuevos

    empresas = set(nuevos["empresa"].tolist())
    resto = previo[~previo["empresa"].isin(empresas)].copy()
    resultado = pd.concat([resto, nuevos], ignore_index=True)
    if logger:
        logger.info("Resumen por empresa acumulado: total=%s", len(resultado))
    return resultado


def _guardar_dataframe_extendido(df, destino):
    """Guarda CSV. Colapsa espacios en texto OCR, pero NO en nombres de archivo."""
    destino = Path(destino)
    sep = _detectar_separador_csv(destino) if destino.exists() else ","
    tabla = df.copy()
    for col in tabla.columns:
        serie = (
            tabla[col]
            .astype(str)
            .str.replace(r"[\r\n]+", " ", regex=True)
        )
        if col not in COLUMNAS_LITERALES:
            serie = serie.str.replace(r"\s+", " ", regex=True)
        tabla[col] = serie.str.strip().replace({"nan": "", "None": ""})
    tabla.to_csv(destino, index=False, encoding="utf-8-sig", sep=sep)


def _guardar_tabla(df, destino):
    tabla = _tabla_publica(df).copy()
    _guardar_dataframe_extendido(tabla, destino)


def clasificar_estado_oficio(datos):
    txt_original = str(datos.get("hoja_ruta") or "")
    conf = str(datos.get("conformidad") or "")
    tiene_hr = bool(txt_original) and len(txt_original) <= 30 and "REFERENCIA" not in txt_original.upper()

    if tiene_hr and conf == "CUENTA":
        return "COMPLETO"
    if tiene_hr and conf == "NO CUENTA":
        return "PARCIAL"
    return "INCOMPLETO"


def distribuir_pdfs_empresa(entrada, df_empresa, logger):
    """Mueve cada PDF a COMPLETO|PARCIAL|INCOMPLETO dentro de su carpeta de empresa."""
    distribuidos = 0

    for _, fila in df_empresa.iterrows():
        estado = fila.get("tiene_hoja_ruta")
        if estado not in ESTADOS_CLASIFICACION:
            continue

        origen = Path(fila["ruta_pdf"])
        if not origen.exists():
            continue

        destino_dir = Path(entrada) / fila["empresa"] / estado
        asegurar_directorio(destino_dir)
        destino = destino_dir / origen.name

        if destino.resolve() == origen.resolve():
            continue

        if destino.exists():
            logger.warning("Ya existe en destino, se omite: %s", destino)
            continue

        shutil.move(str(origen), str(destino))
        distribuidos += 1
        logger.info("Distribuido: %s -> %s/%s", origen.name, fila["empresa"], estado)

    return distribuidos


def _normalizar_nombre(texto):
    return quitar_acentos(str(texto or "")).upper().replace(" ", "")
