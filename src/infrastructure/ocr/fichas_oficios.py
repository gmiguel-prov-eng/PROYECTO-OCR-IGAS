import re
import unicodedata
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

# Paginas del oficio a procesar (1-based).
PAGINAS_OFICIO = [1]
# 2a pasada sobre carpeta PARCIAL: pagina 1 + 2 (datos a veces en la segunda).
PAGINAS_OFICIO_PARCIAL = [1, 2]

# Zonas de recorte en porcentajes: (x1, y1, x2, y2).
ZONA_REFERENCIA = (0.04, 0.36, 0.68, 0.44)
# Si el ASUNTO es largo, REFERENCIA baja: reintento solo si la zona base no halla codigo.
ZONA_REFERENCIA_LARGA = (0.04, 0.36, 0.85, 0.50)
ZONA_CONFORMIDAD = (0.50, 0.47, 0.83, 0.54)
# Parrafo del cuerpo: "...cuenta con / no cuenta con la informacion tecnica..."
ZONA_CONFORMIDAD_CUERPO = (0.08, 0.46, 0.92, 0.64)

ZOOM_RENDER_OFICIO = 2.5
PSM_OCR_OFICIO = 6


def extraer_datos_oficio_pagina(pdf_path, pagina_1based, config_ocr, mostrar_debug=False):
    pdf_path = Path(pdf_path)
    try:
        img = renderizar_pagina_pdf(
            pdf_path,
            pagina_0based=pagina_1based - 1,
            zoom=ZOOM_RENDER_OFICIO,
        )
    except ValueError as exc:
        if mostrar_debug:
            print(f"ADVERTENCIA pagina {pagina_1based}: {exc}")
        return {"hoja_ruta": "", "conformidad": "", "archivo": pdf_path.name}

    texto_ref = ocr_pil(crop_pct(img, ZONA_REFERENCIA), config_ocr, psm=PSM_OCR_OFICIO)
    codigo_referencia = extraer_codigo_referencia(texto_ref)
    zona_usada = "base"

    # Reintento solo si no hay hoja_ruta: ASUNTO multilinea desplaza REFERENCIA.
    if not codigo_referencia:
        texto_ref_larga = ocr_pil(
            crop_pct(img, ZONA_REFERENCIA_LARGA),
            config_ocr,
            psm=PSM_OCR_OFICIO,
        )
        codigo_larga = extraer_codigo_referencia(texto_ref_larga)
        if codigo_larga:
            codigo_referencia = codigo_larga
            texto_ref = texto_ref_larga
            zona_usada = "larga"

    estado_conformidad, zona_conf = detectar_conformidad(img, config_ocr)

    if mostrar_debug:
        print(f"\n[DEBUG] {pdf_path.name} pagina {pagina_1based}")
        print(
            "Referencia:",
            codigo_referencia,
            "| Conformidad:",
            estado_conformidad,
            "| zona_ref:",
            zona_usada,
            "| zona_conf:",
            zona_conf,
        )

    return {
        "hoja_ruta": codigo_referencia,
        "conformidad": estado_conformidad,
        "archivo": pdf_path.name,
    }


def detectar_conformidad(img, config_ocr):
    """
    1) Zona compacta (casillas / texto corto).
    2) Si no hay senal clara, parrafo del cuerpo con 'cuenta con la informacion...'.
    """
    texto_zona = ocr_pil(crop_pct(img, ZONA_CONFORMIDAD), config_ocr, psm=PSM_OCR_OFICIO)
    estado = limpiar_conformidad(texto_zona)
    if estado in {"CUENTA", "NO CUENTA"}:
        return estado, "zona"

    texto_cuerpo = ocr_pil(
        crop_pct(img, ZONA_CONFORMIDAD_CUERPO),
        config_ocr,
        psm=PSM_OCR_OFICIO,
    )
    estado_cuerpo = limpiar_conformidad(texto_cuerpo)
    if estado_cuerpo in {"CUENTA", "NO CUENTA"}:
        return estado_cuerpo, "cuerpo"

    return "NO DETECTADO", "ninguna"


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


def crop_pct(pil_img, box_pct):
    w, h = pil_img.size
    x1, y1, x2, y2 = box_pct
    return pil_img.crop((int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)))


def preprocesar_para_ocr(pil_img, scale=2):
    if cv2 is None or np is None:
        return pil_img

    arr = np.array(pil_img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.bilateralFilter(gray, 5, 55, 55)
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def ocr_pil(pil_img, config_ocr, psm=PSM_OCR_OFICIO):
    proc = preprocesar_para_ocr(pil_img)
    lang = config_ocr.get("languages", "spa+eng")
    config = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
    txt = pytesseract.image_to_string(proc, lang=lang, config=config)
    return limpiar_texto(txt)


def renderizar_pagina_pdf(pdf_path, pagina_0based=0, zoom=2.0):
    doc = fitz.open(pdf_path)
    if pagina_0based >= doc.page_count:
        doc.close()
        raise ValueError(f"El PDF solo tiene {doc.page_count} paginas.")

    page = doc.load_page(pagina_0based)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def quitar_acentos(txt):
    txt = "" if txt is None else str(txt)
    return "".join(
        c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn"
    )


def extraer_codigo_referencia(texto):
    texto = limpiar_texto(texto)
    texto_normal = quitar_acentos(texto).upper()
    texto_plano = re.sub(r"\s+", " ", texto_normal).strip()

    # Preferir formato de expediente: E-033451-2019
    patron_expediente = r"\bE\s*-\s*\d{3,}\s*-\s*\d{4}\b"
    coincidencia = re.search(patron_expediente, texto_plano)
    if coincidencia:
        return re.sub(r"\s+", "", coincidencia.group(0))

    patron_codigo = r"\b(?:N°|NRO\.?|NUMERO|N\.)\s*([A-Z0-9][-A-Z0-9]+)"
    coincidencia = re.search(patron_codigo, texto_plano)
    if coincidencia:
        return coincidencia.group(1).strip()

    patron_respaldo = r"\b[A-Z]\s*-\s*\d+\s*-\s*\d{4}\b"
    coincidencia_respaldo = re.search(patron_respaldo, texto_plano)
    if coincidencia_respaldo:
        return re.sub(r"\s+", "", coincidencia_respaldo.group(0))

    # Sin codigo reconocible: no devolver el OCR crudo (rompe el CSV).
    return ""


def limpiar_conformidad(texto):
    """
    Interpreta conformidad del oficio.
    Prioriza frases del cuerpo ('cuenta con la informacion tecnica' / 'no cuenta con').
    No asume NO CUENTA por defecto ante OCR de zona equivocada.
    """
    if not texto:
        return "NO DETECTADO"

    texto_normal = re.sub(r"\s+", " ", quitar_acentos(texto).lower()).strip()
    if not texto_normal:
        return "NO DETECTADO"

    if re.search(r"\bno\s+cuenta\b", texto_normal) or "no conforme" in texto_normal:
        return "NO CUENTA"
    # Tipico del oficio: "...cuenta con la informacion tecnica requerida..."
    if re.search(r"\bcuenta\s+con\s+la\s+informacion", texto_normal):
        return "CUENTA"
    if re.search(r"\bsi\s+cuenta\b", texto_normal) or re.search(
        r"\bsi\s+conforme\b", texto_normal
    ):
        return "CUENTA"
    if re.search(r"\bconforme\b", texto_normal) and "no conforme" not in texto_normal:
        return "CUENTA"
    return "NO DETECTADO"
