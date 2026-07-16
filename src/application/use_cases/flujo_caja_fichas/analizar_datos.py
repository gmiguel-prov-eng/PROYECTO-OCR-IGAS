import re
import shutil
import unicodedata
from pathlib import Path

import fitz
import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio, listar_archivos


LIMITE_MIN_PAGINAS = 3
LIMITE_MAX_PAGINAS = 30

PALABRAS_DESCARTE_ASUNTO = [
    "DESESTIMIENTO",
    "DESISTIMIENTO",
    "DESISTIR",
    "ANULACION",
    "ANULACIÓN",
    "OFICIO",
]

PALABRAS_REVISION_ASUNTO = [
    "EVAP",
    "CERTIFICACI",
    "AMBIENTAL",
]

COLUMNAS_REPORTE = [
    "hoja_ruta",
    "caja",
    "carpeta",
    "pdf_origen",
    "archivo_pdf",
    "paginas_exportadas",
    "carpeta_destino",
    "estado",
    "motivo",
]

COLUMNAS_TRABAJO = COLUMNAS_REPORTE + ["ruta_pdf"]


def ejecutar(config, logger):
    rutas = config["paths"]
    entrada = rutas["work"]["ocr_fichas"]["extraido"]
    tablas = rutas["work"]["analisis"]["tablas"]
    clasificados = rutas["work"]["analisis"]["pdfs_clasificados"]

    asegurar_directorio(tablas)
    for destino in clasificados.values():
        asegurar_directorio(destino)

    csvs = listar_archivos(entrada, "*.csv")
    logger.info("Proceso 3 iniciado. CSV OCR fichas detectados: %s", len(csvs))

    df = cargar_csvs_ocr(csvs)
    if df.empty:
        reporte_general = guardar_tablas_vacias(tablas)
        return {
            "proceso": "03_analisis_datos",
            "estado": "sin_datos",
            "entrada": entrada,
            "tablas": tablas,
            "pdfs_clasificados": clasificados,
            "csvs_detectados": len(csvs),
            "registros": 0,
            "reporte_general": reporte_general,
        }

    df_preparado = preparar_datos(df)
    reporte_general = clasificar_registros(df_preparado)
    guardar_tablas(reporte_general, tablas)
    limpiar_directorios_clasificacion(clasificados)
    copias = copiar_pdfs_clasificados(reporte_general, clasificados, logger)

    conteos = reporte_general["estado"].value_counts(dropna=False).to_dict()
    logger.info("Proceso 3 completado. Conteos por estado: %s", conteos)

    return {
        "proceso": "03_analisis_datos",
        "estado": "completado",
        "entrada": entrada,
        "tablas": tablas,
        "pdfs_clasificados": clasificados,
        "csvs_detectados": len(csvs),
        "registros": len(reporte_general),
        "seleccionados": int((reporte_general["estado"] == "seleccionado").sum()),
        "revision": int((reporte_general["estado"] == "revision").sum()),
        "no_seleccionados": int((reporte_general["estado"] == "no_seleccionado").sum()),
        "no_considerados": int((reporte_general["estado"] == "no_considerado").sum()),
        "pdfs_copiados": copias,
        "reporte_general": Path(tablas) / "reporte_general.csv",
    }


def cargar_csvs_ocr(csvs):
    frames = []
    for csv_path in csvs:
        df = pd.read_csv(csv_path, encoding="utf-8-sig", sep=None, engine="python")
        df.columns = [str(col).replace("\ufeff", "").strip() for col in df.columns]
        df["csv_origen"] = str(csv_path)
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def preparar_datos(df):
    df = df.copy()

    for columna in ["hoja_ruta", "caja", "carpeta", "pdf_origen", "archivo_pdf", "ruta_pdf"]:
        if columna not in df.columns:
            df[columna] = ""

    df["hoja_ruta"] = df["hoja_ruta"].fillna("").astype(str).str.strip()
    df["n_doc_limpio"] = df.get("n_doc", "").apply(limpiar_nro_documento)
    df["asunto_limpio"] = df.get("asunto", "").apply(limpiar_texto_ocr)
    df["remitente"] = df.get("remitente", "").apply(limpiar_texto_ocr)
    df["texto_busqueda_asunto"] = df["asunto_limpio"].apply(normalizar_texto_busqueda)
    df["texto_busqueda_doc"] = df["n_doc_limpio"].apply(normalizar_texto_busqueda)
    df["paginas_exportadas"] = df["ruta_pdf"].apply(contar_paginas_pdf)
    df["alertas_ocr"] = df.apply(evaluar_alertas_ocr, axis=1)
    df["estado_ocr"] = df["alertas_ocr"].apply(lambda alertas: "revisar" if alertas else "correcto")

    return df


def clasificar_registros(df):
    registros = []
    for _, row in df.iterrows():
        estado, motivo, carpeta_destino = clasificar_fila(row)
        registro = {
            columna: row.get(columna, "")
            for columna in COLUMNAS_TRABAJO
            if columna not in {"estado", "motivo", "carpeta_destino"}
        }
        registro["estado"] = estado
        registro["motivo"] = motivo
        registro["carpeta_destino"] = carpeta_destino
        registros.append(registro)

    reporte = pd.DataFrame(registros)
    reporte = reporte[[col for col in COLUMNAS_TRABAJO if col in reporte.columns]]
    return reporte.sort_values(["estado", "caja", "carpeta", "pdf_origen", "hoja_ruta"]).reset_index(drop=True)


def clasificar_fila(row):
    hoja_ruta = str(row.get("hoja_ruta", "")).strip()
    asunto = row.get("texto_busqueda_asunto", "")
    documento = row.get("texto_busqueda_doc", "")
    paginas = row.get("paginas_exportadas")
    alertas_ocr = row.get("alertas_ocr", [])

    if not hoja_ruta:
        return "no_considerado", "sin_hoja_ruta", "no_considerados"

    motivos_descarte = [palabra for palabra in PALABRAS_DESCARTE_ASUNTO if normalizar_texto_busqueda(palabra) in asunto]
    if motivos_descarte:
        return "no_seleccionado", "asunto_descartado:" + "|".join(motivos_descarte), "no_seleccionados"

    if pd.notna(paginas) and paginas < LIMITE_MIN_PAGINAS:
        return "no_seleccionado", f"menos_de_{LIMITE_MIN_PAGINAS}_paginas", "no_seleccionados"

    motivos_revision = []
    if alertas_ocr:
        motivos_revision.append("ocr:" + "|".join(alertas_ocr))
    if pd.notna(paginas) and paginas > LIMITE_MAX_PAGINAS:
        motivos_revision.append(f"mas_de_{LIMITE_MAX_PAGINAS}_paginas")

    palabras_revision = [palabra for palabra in PALABRAS_REVISION_ASUNTO if normalizar_texto_busqueda(palabra) in asunto]
    if palabras_revision:
        motivos_revision.append("posible_iga_evap:" + "|".join(palabras_revision))

    es_solicitud = bool(re.search(r"\bSOLICITUD\b", documento))
    if not es_solicitud:
        motivos_revision.append("no_es_solicitud")

    if motivos_revision:
        return "revision", " | ".join(motivos_revision), "revision"

    return "seleccionado", "solicitud_candidata", "seleccionados"


def evaluar_alertas_ocr(row):
    alertas = []
    remitente = normalizar_texto_busqueda(row.get("remitente", ""))
    asunto = normalizar_texto_busqueda(row.get("asunto_limpio", ""))
    documento = normalizar_texto_busqueda(row.get("n_doc_limpio", ""))

    if not remitente:
        alertas.append("remitente_vacio")
    if not asunto:
        alertas.append("asunto_vacio")
    if not documento:
        alertas.append("documento_vacio")
    if len(remitente.split()) > 14:
        alertas.append("remitente_muy_largo")
    if "HOJA DE RUTA" in remitente or "FICHA TECNICA" in remitente:
        alertas.append("remitente_con_ruido")
    if asunto and len(asunto) < 12:
        alertas.append("asunto_muy_corto")

    return sorted(set(alertas))


def copiar_pdfs_clasificados(reporte_general, clasificados, logger):
    copias = 0
    for _, row in reporte_general.iterrows():
        carpeta_destino = str(row.get("carpeta_destino", "")).strip() or "no_considerados"
        if carpeta_destino not in clasificados:
            logger.warning(
                "Carpeta destino no reconocida para %s: %s. Se usara no_considerados.",
                row.get("hoja_ruta", ""),
                carpeta_destino,
            )
            carpeta_destino = "no_considerados"

        destino_base = Path(clasificados[carpeta_destino])
        origen = Path(row["ruta_pdf"])

        if not origen.exists():
            logger.warning("No se encontro PDF para copiar: %s", origen)
            continue

        destino = destino_base / str(row["caja"]) / str(row["carpeta"]) / str(row["pdf_origen"]) / origen.name
        asegurar_directorio(destino.parent)
        if not destino.exists() or origen.stat().st_mtime > destino.stat().st_mtime:
            shutil.copy2(origen, destino)
        copias += 1

    return copias


def limpiar_directorios_clasificacion(clasificados):
    for destino in clasificados.values():
        destino = Path(destino)
        if destino.exists():
            shutil.rmtree(destino)
        asegurar_directorio(destino)


def guardar_tablas(reporte_general, tablas):
    tablas = Path(tablas)
    asegurar_directorio(tablas)

    reporte_exportable = preparar_reporte_exportable(reporte_general)
    reporte_exportable.to_csv(tablas / "reporte_general.csv", index=False, encoding="utf-8-sig")
    reporte_exportable[reporte_exportable["estado"] == "seleccionado"].to_csv(
        tablas / "seleccionados.csv", index=False, encoding="utf-8-sig"
    )
    reporte_exportable[reporte_exportable["estado"] == "revision"].to_csv(
        tablas / "revision.csv", index=False, encoding="utf-8-sig"
    )
    reporte_exportable[reporte_exportable["estado"] == "no_seleccionado"].to_csv(
        tablas / "no_seleccionados.csv", index=False, encoding="utf-8-sig"
    )
    reporte_exportable[reporte_exportable["estado"] == "no_considerado"].to_csv(
        tablas / "no_considerados.csv", index=False, encoding="utf-8-sig"
    )


def preparar_reporte_exportable(reporte_general):
    columnas = [col for col in COLUMNAS_REPORTE if col in reporte_general.columns]
    return reporte_general[columnas].copy()


def guardar_tablas_vacias(tablas):
    tablas = Path(tablas)
    asegurar_directorio(tablas)
    reporte = pd.DataFrame(columns=COLUMNAS_REPORTE)
    guardar_tablas(reporte, tablas)
    return tablas / "reporte_general.csv"


def contar_paginas_pdf(path_pdf):
    try:
        path = Path(path_pdf)
        if not path.exists():
            return pd.NA
        with fitz.open(path) as doc:
            return doc.page_count
    except Exception:
        return pd.NA


def es_vacio(value):
    return pd.isna(value) or str(value).strip() == ""


def quitar_tildes(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(char for char in texto if not unicodedata.combining(char))


def normalizar_texto_busqueda(texto):
    if es_vacio(texto):
        return ""
    texto = quitar_tildes(texto).upper()
    return re.sub(r"\s+", " ", texto).strip()


def limpiar_texto_ocr(texto):
    if es_vacio(texto):
        return ""

    txt = unicodedata.normalize("NFKC", str(texto))
    txt = re.sub(r"[\r\n\t]+", " ", txt)
    txt = re.sub(r"[^a-zA-Z0-9ÁÉÍÓÚÜÑáéíóúüñ\s\.\-/&:;,()°]", " ", txt)
    txt = re.sub(r"\s*/\s*", "/", txt)
    txt = re.sub(r"\s*-\s*", " - ", txt)
    txt = re.sub(r"\s*:\s*", ": ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" -:;,.|_")
    return txt


def limpiar_nro_documento(texto):
    txt = limpiar_texto_ocr(texto)
    if not txt:
        return ""

    txt = re.sub(r"\bS\s*[/\-]?\s*N\b", "S/N", txt, flags=re.IGNORECASE)
    normalizaciones = {
        r"\bC\s*A\s*R\s*T\s*\.?\s*A?\b\.?": "CARTA",
        r"\bCART\b\.?": "CARTA",
        r"\bS\s*O\s*L\s*I\s*C\s*I\s*T\s*U\s*D\b": "SOLICITUD",
        r"\bO\s*F\s*I\s*C\s*I\s*O\b": "OFICIO",
        r"\bI\s*N\s*F\s*O\s*R\s*M\s*E\b": "INFORME",
        r"\bM\s*E\s*M\s*O\s*R\s*A\s*N\s*D\s*O\b": "MEMORANDO",
    }
    for patron, reemplazo in normalizaciones.items():
        txt = re.sub(patron, reemplazo, txt, flags=re.IGNORECASE)

    inicio = re.search(r"\b(CARTA|SOLICITUD|OFICIO|INFORME|MEMORANDO|DECLARACION)\b", txt, flags=re.IGNORECASE)
    if inicio:
        txt = txt[inicio.start():].strip()

    match_sn = re.search(r"^(.*?\bS/N\b)", txt, flags=re.IGNORECASE)
    if match_sn:
        txt = match_sn.group(1)

    txt = re.sub(r"\s+", " ", txt).strip(" -:;,.|_")
    return txt
