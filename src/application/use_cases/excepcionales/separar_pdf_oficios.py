"""
Separacion de PDF de oficios.

Misma idea que separar_pdf (fichas), pero el corte inicia cuando se detecta
un oficio (p. ej. "OFICIO N° 10422-2019-MTC/26"), no una hoja de pre-derivacion.
"""

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

# Franja superior mas amplia: el titulo OFICIO suele estar bajo lemas/fecha.
ALTO_PCT_OFICIO = 0.42
ZOOM_OFICIO = 2.0


def ejecutar(config, logger, empresa=None, limite=None):
    config_ocr = dict(config.get("ocr", {}))
    validacion_ocr = validar_idiomas_tesseract(config_ocr, logger=logger)

    rutas = config["paths"]
    entrada = _resolver_entrada_oficio_unido(rutas["input"])
    sep = rutas["work"]["separacion"]
    salida = Path(
        sep.get("separados_oficio")
        or (Path(sep["separados"]).parent / "separados_oficio")
    )
    reportes = Path(
        sep.get("reportes_oficio")
        or (Path(sep["reportes"]).parent / "reportes_oficio")
    )

    asegurar_directorio(salida)
    asegurar_directorio(reportes)

    pdfs = descubrir_pdfs_oficios(entrada, filtro_empresa=empresa, limite=limite)
    logger.info(
        "Separacion oficios iniciado. Entrada(oficio_unido)=%s | Salida=%s | PDFs=%s",
        entrada,
        salida,
        len(pdfs),
    )

    resultados = []
    for item in pdfs:
        resultados.append(
            procesar_pdf_oficios(
                item=item,
                salida_base=salida,
                reportes_base=reportes,
                config_ocr=config_ocr,
                logger=logger,
            )
        )

    reporte_general = reportes / "reporte_general_separacion_oficios.csv"
    pd.DataFrame(resultados).to_csv(reporte_general, index=False, encoding="utf-8-sig")

    estado = "con_errores" if any(r.get("estado") == "error" for r in resultados) else "completado"
    return {
        "proceso": "separar_pdf_oficios",
        "estado": estado,
        "entrada": str(entrada),
        "salida": str(salida),
        "reportes": str(reportes),
        "pdfs_detectados": len(pdfs),
        "pdfs_procesados": len(resultados),
        "oficios_generados": int(sum(r.get("pdfs_generados", 0) for r in resultados)),
        "reporte_general": str(reporte_general),
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def _resolver_entrada_oficio_unido(input_paths):
    """Solo paths.input.oficio_unido. Nunca oficios_origen/DGPRC."""
    valor = input_paths.get("oficio_unido")
    if not valor:
        raise KeyError(
            "Falta paths.input.oficio_unido en el YAML. "
            "Ej: Z:/DOCUMENTACION/.../OFICIOS/TELEFONICA_OTROS"
        )
    entrada = Path(valor)
    if not entrada.exists():
        raise FileNotFoundError(f"No existe oficio_unido: {entrada}")
    if not entrada.is_dir():
        raise NotADirectoryError(f"oficio_unido debe ser una carpeta: {entrada}")
    return entrada


def descubrir_pdfs_oficios(entrada, filtro_empresa=None, limite=None):
    """Solo PDF dentro de oficio_unido (carpeta indicada en config)."""
    entrada = Path(entrada)
    if not entrada.exists():
        return []

    filtro = _normalizar(filtro_empresa) if filtro_empresa else ""
    pdfs = []

    # Preferir PDF en la raiz de oficio_unido; luego subcarpetas propias (no otras rutas).
    candidatos = sorted(entrada.glob("*.pdf")) + sorted(
        p for p in entrada.rglob("*.pdf") if p.parent != entrada
    )
    vistos = set()
    for pdf_path in candidatos:
        if not pdf_path.is_file():
            continue
        key = str(pdf_path.resolve()).lower()
        if key in vistos:
            continue
        vistos.add(key)

        relative = pdf_path.relative_to(entrada)
        empresa = relative.parts[0] if len(relative.parts) >= 2 else entrada.name

        if filtro and filtro not in _normalizar(empresa) and filtro not in _normalizar(pdf_path.stem):
            continue

        pdfs.append(
            {
                "empresa": empresa,
                "pdf_path": pdf_path,
                "pdf_origen": pdf_path.stem,
            }
        )

    if limite:
        pdfs = pdfs[:limite]
    return pdfs


def procesar_pdf_oficios(item, salida_base, reportes_base, config_ocr, logger):
    pdf_path = item["pdf_path"]
    empresa = item["empresa"]
    pdf_origen = item["pdf_origen"]

    logger.info("Procesando PDF oficios: %s", pdf_path)

    # PDF unicos: todos van planos a separados_oficio/{numero_oficio|hoja_ruta}.pdf
    salida_pdf = Path(salida_base)
    reportes_pdf = Path(reportes_base) / empresa / pdf_origen
    asegurar_directorio(salida_pdf)
    asegurar_directorio(reportes_pdf)

    csv_separacion = reportes_pdf / f"{pdf_origen}_indice_oficios_separados.csv"
    if csv_separacion.exists():
        df_existente = pd.read_csv(csv_separacion)
        logger.info("PDF omitido (ya separado): %s", pdf_path.name)
        return _resultado(item, "ya_procesado", salida_pdf, reportes_pdf, len(df_existente), len(df_existente), "")

    try:
        deteccion = detectar_ubicacion_oficios(pdf_path, config_ocr=config_ocr)
        _guardar_json(deteccion, reportes_pdf / f"{pdf_origen}_ubicacion_oficios.json")

        if deteccion["total_oficios_detectados"] == 0:
            logger.warning("No se detectaron oficios en %s", pdf_path.name)
            return _resultado(item, "sin_oficios_detectados", salida_pdf, reportes_pdf, 0, 0, "")

        df_separados = separar_pdf_por_oficios(
            input_pdf=pdf_path,
            oficios_detectados=deteccion["oficios_detectados"],
            output_dir=salida_pdf,
        )
        df_separados.insert(0, "empresa", empresa)
        df_separados.insert(1, "pdf_origen", pdf_origen)
        df_separados.to_csv(csv_separacion, index=False, encoding="utf-8-sig")
        _guardar_json(
            df_separados.to_dict(orient="records"),
            reportes_pdf / f"{pdf_origen}_indice_oficios_separados.json",
        )

        logger.info(
            "PDF oficios procesado: %s | oficios=%s | separados=%s",
            pdf_path.name,
            deteccion["total_oficios_detectados"],
            len(df_separados),
        )
        return _resultado(
            item,
            "completado",
            salida_pdf,
            reportes_pdf,
            deteccion["total_oficios_detectados"],
            len(df_separados),
            "",
        )
    except Exception as exc:
        logger.exception("Error procesando PDF oficios: %s", pdf_path)
        return _resultado(item, "error", salida_pdf, reportes_pdf, 0, 0, str(exc))


def detectar_ubicacion_oficios(
    input_pdf,
    config_ocr=None,
    alto_pct=ALTO_PCT_OFICIO,
    zoom=ZOOM_OFICIO,
    saltar_horizontales=True,
):
    input_pdf = Path(input_pdf)
    tiempo_inicio = time.perf_counter()
    oficios = []
    paginas_ocr = 0
    paginas_horizontales_saltadas = 0

    with fitz.open(input_pdf) as doc:
        total_paginas = doc.page_count
        for i in range(total_paginas):
            page = doc.load_page(i)
            orientacion = "horizontal" if es_horizontal(page) else "vertical"
            if saltar_horizontales and orientacion == "horizontal":
                paginas_horizontales_saltadas += 1
                continue

            texto = extraer_texto_franja_superior(page, alto_pct=alto_pct)
            if not texto or not detectar_inicio_oficio(texto):
                # Refuerzo OCR si texto embebido no alcanza.
                paginas_ocr += 1
                img = renderizar_franja_superior(page, alto_pct=alto_pct, zoom=zoom)
                texto = ocr_imagen(img, config_ocr=config_ocr, psm=6)

            if not detectar_inicio_oficio(texto):
                continue

            oficios.append(
                {
                    "orden": len(oficios) + 1,
                    "archivo_origen": input_pdf.name,
                    "pagina_0based": i,
                    "pagina_1based": i + 1,
                    "orientacion": orientacion,
                    "numero_oficio": extraer_numero_oficio(texto),
                    "hoja_ruta": extraer_hoja_ruta_referencia(texto),
                    "texto_ocr_minimo": texto[:500],
                }
            )

    tiempo_total = time.perf_counter() - tiempo_inicio
    return {
        "archivo_origen": input_pdf.name,
        "ruta_origen": str(input_pdf),
        "total_paginas": total_paginas,
        "paginas_con_ocr": paginas_ocr,
        "paginas_horizontales_saltadas": paginas_horizontales_saltadas,
        "total_oficios_detectados": len(oficios),
        "alto_pct_usado": alto_pct,
        "zoom_usado": zoom,
        "tiempo_total_seg": round(tiempo_total, 2),
        "oficios_detectados": oficios,
    }


def separar_pdf_por_oficios(input_pdf, oficios_detectados, output_dir):
    input_pdf = Path(input_pdf)
    output_dir = Path(output_dir)
    asegurar_directorio(output_dir)

    oficios = sorted(oficios_detectados, key=lambda x: x["pagina_0based"])
    resultados = []

    with fitz.open(input_pdf) as doc:
        total_paginas = doc.page_count
        for idx, item in enumerate(oficios):
            start = item["pagina_0based"]
            end = oficios[idx + 1]["pagina_0based"] - 1 if idx + 1 < len(oficios) else total_paginas - 1
            if start > end:
                continue

            numero = str(item.get("numero_oficio") or "").strip()
            hoja = str(item.get("hoja_ruta") or "").strip()
            # Documento unico: nombre = numero de oficio; si no hay, hoja de ruta.
            base_nombre = nombre_seguro(numero) or nombre_seguro(hoja)
            if not base_nombre:
                base_nombre = f"sin_identificador_{idx + 1:03d}_p{start + 1}"
            output_pdf = obtener_ruta_salida_unica(output_dir, f"{base_nombre}.pdf")

            nuevo = fitz.open()
            nuevo.insert_pdf(doc, from_page=start, to_page=end)
            nuevo.save(output_pdf)
            nuevo.close()

            resultados.append(
                {
                    "orden": idx + 1,
                    "archivo_origen": input_pdf.name,
                    "archivo_salida": output_pdf.name,
                    "ruta_salida": str(output_pdf),
                    "numero_oficio": numero,
                    "hoja_ruta": hoja,
                    "pagina_inicio_1based": start + 1,
                    "pagina_fin_1based": end + 1,
                    "paginas_exportadas": end - start + 1,
                }
            )

    return pd.DataFrame(resultados)


def detectar_inicio_oficio(texto):
    """Marca de inicio de un oficio (pagina portada)."""
    t = normalizar_para_busqueda(texto)
    if not t:
        return False

    # OFICIO N° 10422-... / OFICIO No / OFICIO NRO
    if re.search(r"\bOFICIO\s+N[\s°ºO0\.]*\s*\d{3,}", t):
        return True
    if re.search(r"\bOFICIO\s+NRO\.?\s*\d{3,}", t):
        return True
    # Patron tipico MTC en encabezado junto a OFICIO
    if "OFICIO" in t and re.search(r"\d{3,5}\s*-\s*20\d{2}\s*-?\s*MTC", t):
        return True
    return False


def extraer_numero_oficio(texto):
    t = normalizar_para_busqueda(texto)
    patrones = [
        r"OFICIO\s+N[\s°ºO0\.]*\s*([0-9]{3,5}\s*-\s*20\d{2}\s*-?\s*MTC\s*/?\s*\d+)",
        r"OFICIO\s+NRO\.?\s*([0-9]{3,5}\s*-\s*20\d{2}\s*-?\s*MTC\s*/?\s*\d+)",
        r"\b([0-9]{3,5}\s*-\s*20\d{2}\s*-?\s*MTC\s*/?\s*\d+)\b",
    ]
    for patron in patrones:
        match = re.search(patron, t)
        if match:
            codigo = re.sub(r"\s+", "", match.group(1))
            codigo = codigo.replace("MTC/", "MTC-").replace("MTC", "MTC-")
            codigo = re.sub(r"-{2,}", "-", codigo)
            return codigo
    return ""


def extraer_hoja_ruta_referencia(texto):
    t = normalizar_para_busqueda(texto)
    match = re.search(r"\b(E\s*-\s*\d{4,}\s*-\s*\d{4})\b", t)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    match = re.search(r"\b([A-Z]\s*-\s*\d{4,}\s*-\s*\d{4})\b", t)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    return ""


def limpiar_texto(txt):
    if txt is None:
        return ""
    txt = str(txt).replace("\x0c", " ").replace("\u00ba", "\u00b0")
    txt = txt.replace("\u2013", "-").replace("\u2014", "-")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s+", "\n", txt)
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


def normalizar_para_busqueda(txt):
    txt = unicodedata.normalize("NFKD", str(txt or "").upper())
    return "".join(c for c in txt if not unicodedata.combining(c))


def _normalizar(texto):
    return re.sub(r"[^A-Z0-9]", "", normalizar_para_busqueda(texto))


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


def ocr_imagen(pil_img, config_ocr=None, psm=6):
    proc = preprocesar_para_ocr(pil_img)
    languages = (config_ocr or {}).get("languages", "spa+eng")
    config = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
    return limpiar_texto(pytesseract.image_to_string(proc, lang=languages, config=config))


def es_horizontal(page):
    rect = page.rect
    return rect.width > rect.height


def renderizar_franja_superior(page, alto_pct=ALTO_PCT_OFICIO, zoom=ZOOM_OFICIO):
    rect = page.rect
    clip = fitz.Rect(0, 0, rect.width, rect.height * alto_pct)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def extraer_texto_franja_superior(page, alto_pct=ALTO_PCT_OFICIO):
    rect = page.rect
    clip = fitz.Rect(0, 0, rect.width, rect.height * alto_pct)
    return limpiar_texto(page.get_text("text", clip=clip))


def nombre_seguro(txt):
    txt = str(txt or "").strip()
    if not txt:
        return ""
    txt = txt.replace("/", "-").replace("\\", "-").replace(":", "-")
    txt = re.sub(r'[<>:"/\\|?*]', "", txt)
    txt = re.sub(r"\s+", "_", txt)
    return re.sub(r"_+", "_", txt).strip("_")


def obtener_ruta_salida_unica(output_dir, nombre_salida):
    output_dir = Path(output_dir)
    ruta = output_dir / nombre_salida
    if not ruta.exists():
        return ruta
    stem, suffix = ruta.stem, ruta.suffix
    contador = 2
    while True:
        nueva = output_dir / f"{stem}_{contador}{suffix}"
        if not nueva.exists():
            return nueva
        contador += 1


def _guardar_json(data, path):
    path = Path(path)
    asegurar_directorio(path.parent)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _resultado(item, estado, salida_pdf, reportes_pdf, oficios_detectados, pdfs_generados, error):
    return {
        "empresa": item["empresa"],
        "pdf_origen": item["pdf_origen"],
        "ruta_pdf_origen": str(item["pdf_path"]),
        "estado": estado,
        "oficios_detectados": oficios_detectados,
        "pdfs_generados": pdfs_generados,
        "ruta_salida": str(salida_pdf),
        "ruta_reportes": str(reportes_pdf),
        "error": error,
    }
