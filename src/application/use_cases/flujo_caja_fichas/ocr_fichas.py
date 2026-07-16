import json
import re
import time
import unicodedata
from pathlib import Path

import fitz
import pandas as pd
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from infrastructure.ocr.ocr_tools import validar_idiomas_tesseract
from infrastructure.storage.filesystem import asegurar_directorio

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


COLUMNAS_ORDEN = [
    "caja",
    "carpeta",
    "pdf_origen",
    "archivo_pdf",
    "hoja_ruta",
    "fecha_derivacion",
    "folios",
    "remitente",
    "n_doc",
    "asunto",
    "unidad_organica",
    "responsable",
    "observacion",
    "pagina_hoja_derivacion_1based",
    "ruta_pdf",
]


def ejecutar(config, logger):
    config_ocr = dict(config.get("ocr", {}))
    validacion_ocr = validar_idiomas_tesseract(config_ocr, logger=logger)

    rutas = config["paths"]
    entrada = rutas["work"]["separacion"]["separados"]
    salida = rutas["work"]["ocr_fichas"]["extraido"]
    reportes = rutas["work"]["ocr_fichas"]["reportes"]

    asegurar_directorio(salida)
    asegurar_directorio(reportes)

    grupos = _descubrir_grupos_pdf(entrada)
    logger.info("Proceso 2 iniciado. Grupos detectados: %s", len(grupos))

    resumenes = []
    errores_generales = []
    tiempo_inicio = time.perf_counter()

    for grupo in grupos:
        resumen, errores = _procesar_grupo(
            grupo=grupo,
            salida_base=salida,
            reportes_base=reportes,
            config_ocr=config_ocr,
            logger=logger,
        )
        resumenes.append(resumen)
        errores_generales.extend(errores)

    tiempo_total = time.perf_counter() - tiempo_inicio
    reporte_general = _guardar_reporte_general(resumenes, reportes)
    reporte_errores = _guardar_reporte_errores(errores_generales, reportes)

    estado = "con_errores" if errores_generales else "completado"
    pdfs_detectados = sum(item["pdfs_detectados"] for item in resumenes)
    pdfs_procesados = sum(item["pdfs_procesados"] for item in resumenes)

    return {
        "proceso": "02_ocr_fichas",
        "estado": estado,
        "entrada": entrada,
        "salida": salida,
        "reportes": reportes,
        "grupos_detectados": len(grupos),
        "pdfs_detectados": pdfs_detectados,
        "pdfs_procesados": pdfs_procesados,
        "errores": len(errores_generales),
        "tiempo_total_seg": round(tiempo_total, 2),
        "reporte_general": reporte_general,
        "reporte_errores": reporte_errores,
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def _descubrir_grupos_pdf(entrada):
    entrada = Path(entrada)
    grupos = {}

    if not entrada.exists():
        return []

    for pdf_path in sorted(entrada.rglob("*.pdf")):
        relative = pdf_path.relative_to(entrada)
        if len(relative.parts) < 4:
            continue

        caja, carpeta, pdf_origen = relative.parts[:3]
        key = (caja, carpeta, pdf_origen)
        grupos.setdefault(
            key,
            {
                "caja": caja,
                "carpeta": carpeta,
                "pdf_origen": pdf_origen,
                "pdfs": [],
            },
        )
        grupos[key]["pdfs"].append(pdf_path)

    return list(grupos.values())


def _procesar_grupo(grupo, salida_base, reportes_base, config_ocr, logger):
    caja = grupo["caja"]
    carpeta = grupo["carpeta"]
    pdf_origen = grupo["pdf_origen"]
    pdfs = grupo["pdfs"]

    salida_grupo = Path(salida_base) / caja / carpeta / pdf_origen
    reportes_grupo = Path(reportes_base) / caja / carpeta / pdf_origen
    asegurar_directorio(salida_grupo)
    asegurar_directorio(reportes_grupo)

    csv_salida = salida_grupo / f"{pdf_origen}_ocr_fichas.csv"
    json_salida = reportes_grupo / f"{pdf_origen}_ocr_fichas.json"

    if csv_salida.exists():
        df_existente = pd.read_csv(csv_salida)
        logger.info("Grupo omitido porque ya tiene CSV de OCR fichas: %s", pdf_origen)
        return (
            _resumen_grupo(
                grupo,
                estado="ya_procesado",
                csv_salida=csv_salida,
                pdfs_detectados=len(pdfs),
                pdfs_procesados=len(df_existente),
                errores=0,
            ),
            [],
        )

    resultados = []
    errores = []
    tiempo_inicio = time.perf_counter()
    logger.info("Procesando grupo OCR fichas: %s/%s/%s | PDFs=%s", caja, carpeta, pdf_origen, len(pdfs))

    for pdf_path in pdfs:
        try:
            datos = extraer_datos_hoja_derivacion(pdf_path, config_ocr=config_ocr)
            datos["caja"] = caja
            datos["carpeta"] = carpeta
            datos["pdf_origen"] = pdf_origen
            resultados.append(datos)
        except Exception as exc:
            logger.exception("Error en OCR fichas: %s", pdf_path)
            errores.append(
                {
                    "caja": caja,
                    "carpeta": carpeta,
                    "pdf_origen": pdf_origen,
                    "archivo_pdf": pdf_path.name,
                    "ruta_pdf": str(pdf_path),
                    "error": str(exc),
                }
            )

    df = pd.DataFrame(resultados)
    if not df.empty:
        df = limpiar_dataframe_resultados(df)
        df = df[[col for col in COLUMNAS_ORDEN if col in df.columns]]
    else:
        df = pd.DataFrame(columns=COLUMNAS_ORDEN)

    df.to_csv(csv_salida, index=False, encoding="utf-8-sig")
    _guardar_json(df.to_dict(orient="records"), json_salida)

    if errores:
        _guardar_json(errores, reportes_grupo / f"{pdf_origen}_errores_ocr_fichas.json")

    tiempo_total = time.perf_counter() - tiempo_inicio
    logger.info(
        "Grupo OCR fichas procesado: %s | correctos=%s | errores=%s | tiempo_seg=%s",
        pdf_origen,
        len(df),
        len(errores),
        round(tiempo_total, 2),
    )

    estado = "con_errores" if errores else "completado"
    resumen = _resumen_grupo(
        grupo,
        estado=estado,
        csv_salida=csv_salida,
        pdfs_detectados=len(pdfs),
        pdfs_procesados=len(df),
        errores=len(errores),
    )
    resumen["tiempo_total_seg"] = round(tiempo_total, 2)

    return resumen, errores


def extraer_datos_hoja_derivacion(pdf_path, config_ocr=None, pagina_hoja=0, alto_cabecera=0.32, zoom=2.0):
    pdf_path = Path(pdf_path)

    with fitz.open(pdf_path) as doc:
        if pagina_hoja >= doc.page_count:
            raise ValueError(f"El PDF solo tiene {doc.page_count} paginas.")

        page = doc.load_page(pagina_hoja)
        img = renderizar_pagina(page, zoom=zoom)
        cabecera = crop_pct(img, (0, 0, 1, alto_cabecera))
        texto_cabecera = ocr_pil(cabecera, config_ocr=config_ocr, psm=6)
        metodo_extraccion = "ocr"

        if not detectar_cabecera_hoja_derivacion(texto_cabecera):
            texto_cabecera = extraer_texto_cabecera(page, alto_cabecera=alto_cabecera)
            metodo_extraccion = "texto_pdf"

    datos = extraer_campos_desde_texto(texto_cabecera, pdf_path)
    datos.update(
        {
            "archivo_pdf": pdf_path.name,
            "ruta_pdf": str(pdf_path),
            "pagina_hoja_derivacion_1based": pagina_hoja + 1,
            "metodo_extraccion": metodo_extraccion,
            "texto_cabecera": texto_cabecera,
        }
    )
    return datos


def extraer_campos_desde_texto(texto, pdf_path):
    texto = limpiar_texto(texto)
    texto_compacto = compactar_letras_espaciadas(texto)
    texto_regex = normalizar_texto_regex(texto_compacto)

    hoja_ruta = buscar_regex(r"\b([A-Z]\s*-\s*\d{4,}\s*-\s*\d{4})\b", texto_regex)
    if not hoja_ruta:
        hoja_ruta = buscar_regex(r"\b([A-Z]\s*-\s*\d{4,}\s*-\s*\d{4})\b", Path(pdf_path).stem)
    hoja_ruta = hoja_ruta.replace(" ", "")

    fecha_derivacion = buscar_regex(
        r"FECHA\s+DERIVACION\s*:?\s*(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4}\s+\d{1,2}\s*[:.]\s*\d{2})",
        texto_regex,
    )
    fecha_derivacion = normalizar_fecha(fecha_derivacion)

    folios = buscar_regex(r"FOLIOS?\s*:?\s*(\d+)", texto_regex)

    remitente = extraer_entre(texto_regex, r"REMITENTE\s*:?", r"N\s+DOC\s*:?", r"ASUNTO\s*:?")
    n_doc = extraer_entre(texto_regex, r"N\s+DOC\s*:?", r"ASUNTO\s*:?")
    asunto = extraer_entre(
        texto_regex,
        r"ASUNTO\s*:?",
        r"UNIDADES\s+ORGANICAS\s+Y\s+RESPONSABLES\s*:?",
        r"UNIDAD\s+ORGANICA",
        r"INSTRUCCIONES\s*:?",
    )
    unidad_organica = extraer_entre(texto_regex, r"UNIDAD\s+ORGANICA", r"RESPONSABLE", r"OBSERVACION")
    responsable = extraer_responsable(texto_regex)
    observacion = extraer_observacion(texto_regex)

    if not unidad_organica and "DIRECCION GENERAL" in texto_regex and "AMBIENTALES" in texto_regex:
        unidad_organica = "DIRECCION GENERAL DE ASUNTOS SOCIO-AMBIENTALES"

    return {
        "hoja_ruta": hoja_ruta,
        "fecha_derivacion": fecha_derivacion,
        "folios": folios,
        "remitente": limpiar_remitente_final(remitente),
        "n_doc": limpiar_n_doc_final(n_doc),
        "asunto": limpiar_asunto_final(asunto),
        "unidad_organica": limpiar_unidad_final(unidad_organica),
        "responsable": limpiar_responsable_final(responsable),
        "observacion": limpiar_observacion_final(observacion),
    }


def detectar_cabecera_hoja_derivacion(texto):
    texto = normalizar_texto_regex(compactar_letras_espaciadas(texto))
    return "HOJA DE PRE" in texto or "HOJA DE RUTA" in texto or bool(re.search(r"\b[A-Z]-\d{4,}-\d{4}\b", texto))


def extraer_texto_cabecera(page, alto_cabecera=0.32):
    rect = page.rect
    clip = fitz.Rect(0, 0, rect.width, rect.height * alto_cabecera)
    return limpiar_texto(page.get_text("text", clip=clip))


def renderizar_pagina(page, zoom=2.0):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def crop_pct(pil_img, box_pct):
    width, height = pil_img.size
    x1, y1, x2, y2 = box_pct
    return pil_img.crop((int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)))


def preprocesar_para_ocr(pil_img, scale=2):
    if cv2 is not None and np is not None:
        arr = np.array(pil_img.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        if scale != 1:
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 5, 55, 55)
        return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    img = pil_img.convert("L")
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.BICUBIC)
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    return img.point(lambda value: 255 if value > 170 else 0)


def ocr_pil(pil_img, config_ocr=None, psm=6):
    proc = preprocesar_para_ocr(pil_img)
    languages = (config_ocr or {}).get("languages", "spa+eng")
    config = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
    return limpiar_texto(pytesseract.image_to_string(proc, lang=languages, config=config))


def limpiar_texto(txt):
    if txt is None:
        return ""

    txt = str(txt)
    txt = txt.replace("\x0c", " ")
    txt = txt.replace("\u00ba", "\u00b0")
    txt = txt.replace("\u2013", "-")
    txt = txt.replace("\u2014", "-")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s+", "\n", txt)
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def compactar_letras_espaciadas(texto):
    def compactar(match):
        return match.group(0).replace(" ", "")

    return re.sub(r"\b(?:[A-Za-zÁÉÍÓÚáéíóúÑñ]\s+){2,}[A-Za-zÁÉÍÓÚáéíóúÑñ]\b", compactar, texto)


def normalizar_texto_regex(texto):
    texto = unicodedata.normalize("NFKD", limpiar_texto(texto))
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    texto = texto.upper()
    texto = re.sub(r"N\s*[^A-Z0-9]{0,3}\s*D\s*[O0]\s*[CE]\s*:?", "N DOC:", texto)
    texto = re.sub(r"A\s*SU\s*N\s*TO\s*[:\\-]?", "ASUNTO:", texto)
    texto = re.sub(r"F\s*[O0°º]\s*L?I?O?S\s*:?", "FOLIOS:", texto)
    texto = texto.replace("F°LIOS", "FOLIOS")
    texto = texto.replace("F°»°*", "FOLIOS")
    texto = texto.replace("FOLLOS", "FOLIOS")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\s*\n\s*", "\n", texto)
    return texto.strip()


def buscar_regex(patron, texto, grupo=1):
    match = re.search(patron, texto, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return limpiar_texto(match.group(grupo))


def extraer_entre(texto, inicio, *finales):
    match_inicio = re.search(inicio, texto, flags=re.IGNORECASE | re.DOTALL)
    if not match_inicio:
        return ""

    start = match_inicio.end()
    end = len(texto)
    for final in finales:
        match_final = re.search(final, texto[start:], flags=re.IGNORECASE | re.DOTALL)
        if match_final:
            end = min(end, start + match_final.start())

    return limpiar_texto(texto[start:end])


def extraer_responsable(texto):
    candidatos = re.findall(
        r"\b([A-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ]+){2,6})\b",
        texto,
        flags=re.IGNORECASE,
    )
    descartes = {
        "HOJA DE PRE DERIVACION",
        "UNIDADES ORGANICAS Y RESPONSABLES",
        "UNIDAD ORGANICA RESPONSABLE OBSERVACION",
        "DIRECCION GENERAL DE ASUNTOS SOCIO",
        "ASUNTOS SOCIO AMBIENTALES",
        "CONOCIMIENTO Y FINES",
        "ADJUNTAR ANTECEDENTES",
        "EMITIR OPINION",
        "NOTIFICAR AL INTERESADO",
        "PREPARAR RESPUESTA",
        "PROYECTAR RESOLUCION",
    }

    for candidato in candidatos:
        candidato = limpiar_texto_resultado(candidato)
        if candidato and candidato not in descartes and not candidato.startswith("REMITE "):
            if any(nombre in candidato.split() for nombre in ("PASTOR", "MIRIAN", "MARIBEL", "HUMBERTO")):
                return candidato

    return ""


def extraer_observacion(texto):
    observacion = extraer_entre(texto, r"OBSERVACION", r"INSTRUCCIONES\s*:?", r"DERIVACIONES\s+A\s*:")
    return observacion


def normalizar_fecha(txt):
    txt = limpiar_texto_resultado(txt)
    txt = txt.replace(" ", "")
    txt = txt.replace(".", ":")
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(\d{1,2}):(\d{2})", txt)
    if match:
        dia, mes, anio, hora, minuto = match.groups()
        return f"{dia.zfill(2)}/{mes.zfill(2)}/{anio} {hora.zfill(2)}:{minuto}"
    return txt


def limpiar_texto_resultado(txt):
    if pd.isna(txt):
        return ""

    txt = str(txt)
    reemplazos = {
        "\x0c": " ",
        "\u00ba": "\u00b0",
        "N*": "N\u00b0",
        "N\u00ba": "N\u00b0",
        "N o": "N\u00b0",
        "N \u00b0": "N\u00b0",
        "\u2013": "-",
        "\u2014": "-",
        "_": " ",
    }

    for old, new in reemplazos.items():
        txt = txt.replace(old, new)

    txt = re.sub(r"\b[fF]\b", " ", txt)
    txt = re.sub(r"(?:\b\d+\b\s*){3,}", " ", txt)
    txt = re.sub(r"[\[\]{}|_;,.\-]{4,}.*$", " ", txt)
    txt = re.sub(r"(?:\s*[-\];\[]\s*){3,}", " ", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", " ", txt)
    txt = re.sub(r"\s{2,}", " ", txt)
    return txt.strip(" :-\n\t")


def quitar_ruido_etiquetas(txt):
    txt = limpiar_texto_resultado(txt)
    patrones = [
        r"\bREMITENTE\s*:?",
        r"\bASUNTO\s*:?",
        r"\bFECHA\s+DERIVACION\s*:?",
        r"\bFOLIOS\s*:?",
        r"\bHOJA\s+DE\s+RUTA\s*N\s*[°ºO]?\s*:?",
        r"\bN\s*[°ºO*]?\s*DOC\s*:?",
        r"\bUNIDAD\s+ORGANICA\b",
        r"\bUNIDADES\s+ORGANICAS\s+Y\s+RESPONSABLES\s*:?",
        r"\bRESPONSABLE\b",
        r"\bOBSERVACION\b",
    ]

    for patron in patrones:
        txt = re.sub(patron, " ", txt, flags=re.IGNORECASE)

    return re.sub(r"\s{2,}", " ", txt).strip(" :-\n\t")


def limpiar_remitente_final(txt):
    txt = quitar_ruido_etiquetas(txt)
    txt = re.split(r"\bN\s*[°ºO*]?\s*D\s*[O0]\s*[CE]\s*:?", txt, flags=re.IGNORECASE)[0]
    return limpiar_texto_resultado(txt)


def limpiar_n_doc_final(txt):
    txt = limpiar_texto_resultado(txt)
    txt = re.sub(r"\bN\s*[°ºO*]?\s*D\s*[O0]\s*[CE]\s*:?", "", txt, flags=re.IGNORECASE)
    txt = re.split(r"\bASUNTO\s*:?", txt, flags=re.IGNORECASE)[0]
    txt = re.split(r"\bA\s*SU\s*N\s*TO\s*:?", txt, flags=re.IGNORECASE)[0]
    txt = re.sub(r"\b(?:EEE|EE|T)\b$", "", txt, flags=re.IGNORECASE)
    return limpiar_texto_resultado(txt)


def limpiar_asunto_final(txt):
    txt = quitar_ruido_etiquetas(txt)
    cortes = [
        r"\bUNIDADES\s+ORGANICAS\b",
        r"\bUNIDAD\s+ORGANICA\b",
        r"\bRESPONSABLE\b",
        r"\bOBSERVACION\b",
        r"\bDIRECCION\s+GENERAL\b",
        r"\bINSTRUCCIONES\s*:?",
    ]

    for corte in cortes:
        txt = re.split(corte, txt, flags=re.IGNORECASE)[0]

    return limpiar_texto_resultado(txt)


def limpiar_unidad_final(txt):
    txt = quitar_ruido_etiquetas(txt)
    txt = re.sub(r"^\s*\d+\s+", "", txt)
    txt = txt.replace("SOCIO- AMBIENT", "SOCIO-AMBIENT")
    txt = txt.replace("SOCIO - AMBIENT", "SOCIO-AMBIENT")
    txt = txt.replace("SOCIO- AMBIENTALES", "SOCIO-AMBIENTALES")
    return limpiar_texto_resultado(txt)


def limpiar_responsable_final(txt):
    txt = quitar_ruido_etiquetas(txt)
    txt = re.sub(r"^\s*\d+\s+", "", txt)
    txt = re.sub(r"\bAMBIENTALES\b", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bINSTRUC+IONES?\b", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bINSTRUC+TONES?\b", "", txt, flags=re.IGNORECASE)
    return limpiar_texto_resultado(txt)


def limpiar_observacion_final(txt):
    txt = quitar_ruido_etiquetas(txt)
    txt = re.sub(r"^\s*\d+\s+", "", txt)
    return limpiar_texto_resultado(txt)


def limpiar_dataframe_resultados(df):
    df_limpio = df.copy()
    for columna, funcion in {
        "remitente": limpiar_remitente_final,
        "n_doc": limpiar_n_doc_final,
        "asunto": limpiar_asunto_final,
        "unidad_organica": limpiar_unidad_final,
        "responsable": limpiar_responsable_final,
        "observacion": limpiar_observacion_final,
    }.items():
        if columna in df_limpio.columns:
            df_limpio[columna] = df_limpio[columna].apply(funcion)

    return df_limpio


def _guardar_json(data, path):
    path = Path(path)
    asegurar_directorio(path.parent)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _guardar_reporte_general(resumenes, reportes):
    reportes = Path(reportes)
    asegurar_directorio(reportes)
    reporte_general = reportes / "reporte_general_ocr_fichas.csv"
    pd.DataFrame(resumenes).to_csv(reporte_general, index=False, encoding="utf-8-sig")
    return reporte_general


def _guardar_reporte_errores(errores, reportes):
    reportes = Path(reportes)
    asegurar_directorio(reportes)
    reporte_errores = reportes / "errores_ocr_fichas.csv"
    pd.DataFrame(errores).to_csv(reporte_errores, index=False, encoding="utf-8-sig")
    return reporte_errores


def _resumen_grupo(grupo, estado, csv_salida, pdfs_detectados, pdfs_procesados, errores):
    return {
        "caja": grupo["caja"],
        "carpeta": grupo["carpeta"],
        "pdf_origen": grupo["pdf_origen"],
        "estado": estado,
        "pdfs_detectados": pdfs_detectados,
        "pdfs_procesados": pdfs_procesados,
        "errores": errores,
        "csv_salida": str(csv_salida),
    }
