import re
import shutil
import unicodedata
from pathlib import Path

import fitz
import pandas as pd
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from infrastructure.ocr.ocr_tools import validar_idiomas_tesseract
from infrastructure.storage.filesystem import asegurar_directorio, listar_pdfs

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


PAGINAS_OCR_RESPALDO = [3, 4]


def tiene_informe(nombre_informe):
    if nombre_informe is None or pd.isna(nombre_informe):
        return False

    return bool(str(nombre_informe).strip())


def ejecutar(config, logger):
    config_ocr = dict(config.get("ocr", {}))
    validacion_ocr = validar_idiomas_tesseract(config_ocr, logger=logger)

    rutas = config["paths"]
    entrada = rutas["work"]["analisis"]["pdfs_clasificados"]["seleccionados"]
    resultados = rutas["output"]["resultados_finales"]["resultados"]
    expedientes = rutas["output"]["resultados_finales"]["expedientes"]
    inventario = rutas["output"]["resultados_finales"]["inventario"]

    asegurar_directorio(resultados)
    asegurar_directorio(expedientes)
    asegurar_directorio(inventario)

    pdfs = listar_pdfs(entrada)
    logger.info("Proceso 4 iniciado. PDFs seleccionados detectados: %s", len(pdfs))

    registros = []
    for pdf_path in pdfs:
        try:
            registros.append(procesar_pdf_solicitud(pdf_path, entrada, config_ocr))
        except Exception as exc:
            logger.exception("Error procesando solicitud: %s", pdf_path)
            registros.append(registro_error(pdf_path, entrada, str(exc)))

    df = pd.DataFrame(registros)
    if df.empty:
        df = pd.DataFrame(columns=columnas_salida())

    df["tiene_informe"] = df["nombre_informe"].apply(tiene_informe)
    guardar_salidas(df, resultados, inventario)
    copiados = copiar_expedientes_con_informe(df, expedientes, logger)

    return {
        "proceso": "04_ocr_solicitudes",
        "estado": "completado",
        "entrada": entrada,
        "resultados": resultados,
        "expedientes": expedientes,
        "inventario": inventario,
        "pdfs_detectados": len(pdfs),
        "pdfs_con_informe_tecnico": int(df["tiene_informe"].sum()),
        "pdfs_sin_informe_tecnico": int((~df["tiene_informe"]).sum()),
        "expedientes_copiados": copiados,
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def procesar_pdf_solicitud(pdf_path, entrada_base, config_ocr):
    pdf_path = Path(pdf_path)
    relative = pdf_path.relative_to(entrada_base)
    caja, carpeta, pdf_origen = extraer_trazabilidad(relative)
    hoja_ruta = obtener_hoja_ruta_pdf(pdf_path)

    texto_pdf, pagina_texto = extraer_texto_pdf_con_informe_tecnico(pdf_path)
    nombre_informe = extraer_nombre_informe_tecnico(texto_pdf)
    metodo = "texto_pdf" if nombre_informe else ""
    pagina_detectada = pagina_texto

    if not nombre_informe:
        texto_ocr, pagina_ocr = extraer_ocr_paginas_respaldo(pdf_path, config_ocr)
        nombre_informe = extraer_nombre_informe_tecnico(texto_ocr)
        metodo = "ocr" if nombre_informe else "no_detectado"
        pagina_detectada = pagina_ocr if nombre_informe else ""

    return {
        "hoja_ruta": hoja_ruta,
        "nombre_informe": nombre_informe,
        "caja": caja,
        "carpeta": carpeta,
        "pdf_origen": pdf_origen,
        "archivo_pdf": pdf_path.name,
        "pagina_detectada": pagina_detectada,
        "metodo_deteccion": metodo,
        "ruta_pdf": str(pdf_path),
        "error": "",
    }


def extraer_trazabilidad(relative_path):
    parts = relative_path.parts
    caja = parts[0] if len(parts) > 0 else ""
    carpeta = parts[1] if len(parts) > 1 else ""
    pdf_origen = parts[2] if len(parts) > 2 else ""
    return caja, carpeta, pdf_origen


def obtener_hoja_ruta_pdf(pdf_path):
    match = re.search(r"([A-Z]\s*-\s*\d{4,}\s*-\s*\d{4})", Path(pdf_path).stem, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).replace(" ", "").upper()


def extraer_texto_pdf_con_informe_tecnico(pdf_path):
    textos = []
    primera_pagina = ""
    with fitz.open(pdf_path) as doc:
        for index in range(doc.page_count):
            texto = doc.load_page(index).get_text("text")
            textos.append(texto)
            if not primera_pagina and contiene_informe_tecnico(texto):
                primera_pagina = index + 1

    return "\n".join(textos), primera_pagina


def extraer_ocr_paginas_respaldo(pdf_path, config_ocr):
    textos = []
    pagina_detectada = ""
    with fitz.open(pdf_path) as doc:
        for pagina_1based in PAGINAS_OCR_RESPALDO:
            pagina_0based = pagina_1based - 1
            if pagina_0based >= doc.page_count:
                continue

            page = doc.load_page(pagina_0based)
            img = renderizar_pagina(page, zoom=2.2)
            texto = ocr_pil(img, config_ocr=config_ocr, psm=6)
            textos.append(texto)
            if not pagina_detectada and contiene_informe_tecnico(texto):
                pagina_detectada = pagina_1based

    return "\n".join(textos), pagina_detectada


def contiene_informe_tecnico(texto):
    return bool(re.search(r"\bINFORME\s+TECNICO\b", normalizar_texto_busqueda(texto)))


def extraer_nombre_informe_tecnico(texto):
    if not contiene_informe_tecnico(texto):
        return ""

    lineas = [limpiar_texto(linea) for linea in str(texto).splitlines() if limpiar_texto(linea)]

    for linea in lineas:
        if contiene_informe_tecnico(linea):
            nombre = limpiar_nombre_informe(linea)
            if nombre:
                return nombre

    texto_plano = limpiar_texto(re.sub(r"\s+", " ", str(texto)))
    normal = normalizar_texto_busqueda(texto_plano)
    match = re.search(r"\bINFORME\s+TECNICO\b.{0,120}", normal)
    if match:
        return limpiar_nombre_informe(match.group(0))

    return "INFORME TECNICO"


def limpiar_nombre_informe(texto):
    texto = limpiar_texto(texto)
    if not texto:
        return ""

    normal = normalizar_texto_busqueda(texto)
    index = normal.find("INFORME TECNICO")
    if index < 0:
        return ""

    # Usa el texto normalizado desde la frase clave para evitar variantes OCR con tildes o simbolos.
    nombre = normal[index:]
    nombre = re.split(r"\b(?:A|ASUNTO|REFERENCIA|FECHA|DE\s+FECHA)\b\s*:", nombre)[0]
    nombre = normalizar_codigo_informe(nombre)
    nombre = re.sub(r"\s+", " ", nombre).strip(" .,:;-")
    return nombre


def normalizar_codigo_informe(nombre):
    nombre = nombre.replace("°", " ")
    nombre = re.sub(r"\bN\s*[°ºO]?\s*", "N ", nombre)
    nombre = re.sub(r"(?<=\d)Q(?=\d)", "0", nombre)
    nombre = re.sub(r"(?<=\.)Q(?=\d)", "0", nombre)
    nombre = re.sub(r"(?<=\d)\s+(?=\d)", "", nombre)
    nombre = re.sub(r"\bM\s*T\s*C\b", "MTC", nombre)
    nombre = re.sub(r"\s*([/.\-])\s*", r"\1", nombre)
    return nombre


def renderizar_pagina(page, zoom=2.2):
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


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


def quitar_acentos(txt):
    txt = "" if txt is None else str(txt)
    return "".join(
        char for char in unicodedata.normalize("NFD", txt)
        if unicodedata.category(char) != "Mn"
    )


def normalizar_texto_busqueda(texto):
    texto = quitar_acentos(texto).upper()
    texto = re.sub(r"[^A-Z0-9/._\- ]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def guardar_salidas(df, resultados, inventario):
    resultados = Path(resultados)
    inventario = Path(inventario)
    asegurar_directorio(resultados)
    asegurar_directorio(inventario)

    columnas_exportables = [
        "hoja_ruta",
        "nombre_informe",
        "tiene_informe",
        "caja",
        "carpeta",
        "pdf_origen",
        "archivo_pdf",
        "pagina_detectada",
        "metodo_deteccion",
        "error",
    ]
    df_export = df[[col for col in columnas_exportables if col in df.columns]].copy()
    df_export.to_csv(resultados / "resultados_ocr_solicitudes.csv", index=False, encoding="utf-8-sig")
    df_export.to_csv(inventario / "inventario_general.csv", index=False, encoding="utf-8-sig")
    df_export[df_export["tiene_informe"]].to_csv(
        inventario / "inventario_final.csv",
        index=False,
        encoding="utf-8-sig",
    )


def copiar_expedientes_con_informe(df, expedientes, logger):
    expedientes = Path(expedientes)
    if expedientes.exists():
        shutil.rmtree(expedientes)
    asegurar_directorio(expedientes)

    copiados = 0
    for _, row in df[df["tiene_informe"]].iterrows():
        origen = Path(row["ruta_pdf"])
        if not origen.exists():
            logger.warning("No se encontro expediente con informe para copiar: %s", origen)
            continue

        destino = expedientes / str(row["caja"]) / str(row["carpeta"]) / str(row["pdf_origen"]) / origen.name
        asegurar_directorio(destino.parent)
        shutil.copy2(origen, destino)
        copiados += 1

    return copiados


def registro_error(pdf_path, entrada_base, error):
    pdf_path = Path(pdf_path)
    relative = pdf_path.relative_to(entrada_base)
    caja, carpeta, pdf_origen = extraer_trazabilidad(relative)
    return {
        "hoja_ruta": obtener_hoja_ruta_pdf(pdf_path),
        "nombre_informe": "",
        "caja": caja,
        "carpeta": carpeta,
        "pdf_origen": pdf_origen,
        "archivo_pdf": pdf_path.name,
        "pagina_detectada": "",
        "metodo_deteccion": "error",
        "ruta_pdf": str(pdf_path),
        "error": error,
    }


def columnas_salida():
    return [
        "hoja_ruta",
        "nombre_informe",
        "tiene_informe",
        "caja",
        "carpeta",
        "pdf_origen",
        "archivo_pdf",
        "pagina_detectada",
        "metodo_deteccion",
        "ruta_pdf",
        "error",
    ]
