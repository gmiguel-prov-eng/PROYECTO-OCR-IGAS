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


def ejecutar(config, logger):
    config_ocr = dict(config.get("ocr", {}))
    validacion_ocr = validar_idiomas_tesseract(config_ocr, logger=logger)

    rutas = config["paths"]
    entrada = rutas["input"]["cajas"]
    salida = rutas["work"]["separacion"]["separados"]
    reportes = rutas["work"]["separacion"]["reportes"]

    asegurar_directorio(salida)
    asegurar_directorio(reportes)

    pdfs = _descubrir_pdfs_originales(entrada)
    logger.info("Proceso 1 iniciado. PDFs originales detectados: %s", len(pdfs))

    resultados_generales = []

    for item in pdfs:
        resultado = _procesar_pdf_original(
            item=item,
            salida_base=salida,
            reportes_base=reportes,
            config_ocr=config_ocr,
            logger=logger,
        )
        resultados_generales.append(resultado)

    reporte_general = _guardar_reporte_general(resultados_generales, reportes)
    estado = "con_errores" if any(item["estado"] == "error" for item in resultados_generales) else "completado"

    return {
        "proceso": "01_separacion_pdf",
        "estado": estado,
        "entrada": entrada,
        "salida": salida,
        "reportes": reportes,
        "pdfs_detectados": len(pdfs),
        "pdfs_procesados": len(resultados_generales),
        "reporte_general": reporte_general,
        "ocr_languages": validacion_ocr["effective_languages"],
        "ocr_missing_languages": validacion_ocr["missing_languages"],
    }


def _descubrir_pdfs_originales(entrada):
    entrada = Path(entrada)
    if not entrada.exists():
        return []

    pdfs = []
    for pdf_path in sorted(entrada.rglob("*.pdf")):
        relative = pdf_path.relative_to(entrada)
        if len(relative.parts) < 3:
            continue

        caja = relative.parts[0]
        carpeta = relative.parts[1]
        pdfs.append(
            {
                "caja": caja,
                "carpeta": carpeta,
                "pdf_path": pdf_path,
                "pdf_origen": pdf_path.stem,
            }
        )

    return pdfs


def _procesar_pdf_original(item, salida_base, reportes_base, config_ocr, logger):
    pdf_path = item["pdf_path"]
    caja = item["caja"]
    carpeta = item["carpeta"]
    pdf_origen = item["pdf_origen"]

    logger.info("Procesando PDF original: %s", pdf_path)

    salida_pdf = Path(salida_base) / caja / carpeta / pdf_origen
    reportes_pdf = Path(reportes_base) / caja / carpeta / pdf_origen
    asegurar_directorio(salida_pdf)
    asegurar_directorio(reportes_pdf)

    csv_separacion = reportes_pdf / f"{pdf_origen}_indice_pdf_separados.csv"
    if csv_separacion.exists():
        df_existente = pd.read_csv(csv_separacion)
        logger.info("PDF omitido porque ya tiene reporte de separacion: %s", pdf_path.name)
        return _resultado_pdf(
            item,
            estado="ya_procesado",
            salida_pdf=salida_pdf,
            reportes_pdf=reportes_pdf,
            hojas_detectadas=len(df_existente),
            pdfs_generados=len(df_existente),
            error="",
        )

    try:
        deteccion = detectar_ubicacion_hojas_ruta(pdf_path, config_ocr=config_ocr)

        json_ubicaciones = reportes_pdf / f"{pdf_origen}_ubicacion_hojas_ruta.json"
        _guardar_json(deteccion, json_ubicaciones)

        if deteccion["total_hojas_detectadas"] == 0:
            logger.warning("No se detectaron hojas de ruta en %s", pdf_path.name)
            return _resultado_pdf(
                item,
                estado="sin_hojas_detectadas",
                salida_pdf=salida_pdf,
                reportes_pdf=reportes_pdf,
                hojas_detectadas=0,
                pdfs_generados=0,
                error="",
            )

        df_separados = separar_pdf_por_ubicaciones(
            input_pdf=pdf_path,
            hojas_detectadas=deteccion["hojas_detectadas"],
            output_dir=salida_pdf,
        )

        json_separacion = reportes_pdf / f"{pdf_origen}_indice_pdf_separados.json"

        df_separados.insert(0, "caja", caja)
        df_separados.insert(1, "carpeta", carpeta)
        df_separados.insert(2, "pdf_origen", pdf_origen)
        df_separados.to_csv(csv_separacion, index=False, encoding="utf-8-sig")
        _guardar_json(df_separados.to_dict(orient="records"), json_separacion)

        logger.info(
            "PDF procesado: %s | hojas=%s | separados=%s",
            pdf_path.name,
            deteccion["total_hojas_detectadas"],
            len(df_separados),
        )

        return _resultado_pdf(
            item,
            estado="completado",
            salida_pdf=salida_pdf,
            reportes_pdf=reportes_pdf,
            hojas_detectadas=deteccion["total_hojas_detectadas"],
            pdfs_generados=len(df_separados),
            error="",
        )

    except Exception as exc:
        logger.exception("Error procesando %s", pdf_path)
        return _resultado_pdf(
            item,
            estado="error",
            salida_pdf=salida_pdf,
            reportes_pdf=reportes_pdf,
            hojas_detectadas=0,
            pdfs_generados=0,
            error=str(exc),
        )


def detectar_ubicacion_hojas_ruta(
    input_pdf,
    config_ocr=None,
    alto_pct=0.16,
    zoom=2.0,
    saltar_horizontales=True,
):
    input_pdf = Path(input_pdf)
    tiempo_inicio = time.perf_counter()

    hojas_detectadas = []
    paginas_revisadas = 0
    paginas_ocr = 0
    paginas_horizontales_saltadas = 0

    with fitz.open(input_pdf) as doc:
        total_paginas = doc.page_count

        for i in range(total_paginas):
            page = doc.load_page(i)
            paginas_revisadas += 1

            orientacion = "horizontal" if es_horizontal(page) else "vertical"
            if saltar_horizontales and orientacion == "horizontal":
                paginas_horizontales_saltadas += 1
                continue

            texto = extraer_texto_franja_superior(page, alto_pct=alto_pct)
            if not texto:
                paginas_ocr += 1
                img_franja = renderizar_franja_superior(page, alto_pct=alto_pct, zoom=zoom)
                texto = ocr_imagen(img_franja, config_ocr=config_ocr, psm=6)

            if detectar_hoja_pre_derivacion(texto):
                hoja_ruta = extraer_codigo_hoja_ruta(texto)
                hojas_detectadas.append(
                    {
                        "orden": len(hojas_detectadas) + 1,
                        "archivo_origen": input_pdf.name,
                        "pagina_0based": i,
                        "pagina_1based": i + 1,
                        "orientacion": orientacion,
                        "hoja_ruta": hoja_ruta,
                        "texto_ocr_minimo": texto,
                    }
                )

    tiempo_total_seg = time.perf_counter() - tiempo_inicio
    tiempo_promedio_total = tiempo_total_seg / total_paginas if total_paginas > 0 else 0
    tiempo_promedio_ocr = tiempo_total_seg / paginas_ocr if paginas_ocr > 0 else 0

    return {
        "archivo_origen": input_pdf.name,
        "ruta_origen": str(input_pdf),
        "total_paginas": total_paginas,
        "paginas_revisadas": paginas_revisadas,
        "paginas_con_ocr": paginas_ocr,
        "paginas_horizontales_saltadas": paginas_horizontales_saltadas,
        "total_hojas_detectadas": len(hojas_detectadas),
        "alto_pct_usado": alto_pct,
        "zoom_usado": zoom,
        "saltar_horizontales": saltar_horizontales,
        "tiempo_total_seg": round(tiempo_total_seg, 2),
        "tiempo_total_min": round(tiempo_total_seg / 60, 2),
        "tiempo_promedio_por_pagina_seg": round(tiempo_promedio_total, 3),
        "tiempo_promedio_por_pagina_ocr_seg": round(tiempo_promedio_ocr, 3),
        "hojas_detectadas": hojas_detectadas,
    }


def separar_pdf_por_ubicaciones(input_pdf, hojas_detectadas, output_dir):
    input_pdf = Path(input_pdf)
    output_dir = Path(output_dir)
    asegurar_directorio(output_dir)

    hojas = sorted(hojas_detectadas, key=lambda x: x["pagina_0based"])
    resultados = []

    with fitz.open(input_pdf) as doc:
        total_paginas = doc.page_count

        for idx, item in enumerate(hojas):
            start = item["pagina_0based"]
            end = hojas[idx + 1]["pagina_0based"] - 1 if idx + 1 < len(hojas) else total_paginas - 1

            if start > end:
                continue

            hoja_ruta = item.get("hoja_ruta", "")
            hoja_ruta_safe = nombre_seguro(hoja_ruta)
            if hoja_ruta_safe:
                nombre_salida = f"{hoja_ruta_safe}.pdf"
            else:
                nombre_salida = f"sin_hoja_ruta_{idx + 1:03d}_pagina_{start + 1}.pdf"

            output_pdf = obtener_ruta_salida_unica(output_dir, nombre_salida)

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
                    "hoja_ruta": hoja_ruta,
                    "pagina_inicio_0based": start,
                    "pagina_fin_0based": end,
                    "pagina_inicio_1based": start + 1,
                    "pagina_fin_1based": end + 1,
                    "paginas_exportadas": end - start + 1,
                }
            )

    return pd.DataFrame(resultados)


def limpiar_texto(txt):
    if txt is None:
        return ""

    txt = txt.replace("\x0c", " ")
    txt = txt.replace("\u00ba", "\u00b0")
    txt = txt.replace("\u2013", "-")
    txt = txt.replace("\u2014", "-")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s+", "\n", txt)
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)

    return txt.strip()


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
    texto = pytesseract.image_to_string(proc, lang=languages, config=config)
    return limpiar_texto(texto)


def es_horizontal(page):
    rect = page.rect
    return rect.width > rect.height


def renderizar_franja_superior(page, alto_pct=0.16, zoom=2.0):
    rect = page.rect
    clip = fitz.Rect(0, 0, rect.width, rect.height * alto_pct)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def extraer_texto_franja_superior(page, alto_pct=0.16):
    rect = page.rect
    clip = fitz.Rect(0, 0, rect.width, rect.height * alto_pct)
    texto = page.get_text("text", clip=clip)
    return limpiar_texto(texto)


def normalizar_para_busqueda(txt):
    txt = unicodedata.normalize("NFKD", txt.upper())
    return "".join(char for char in txt if not unicodedata.combining(char))


def detectar_hoja_pre_derivacion(texto):
    texto_normalizado = normalizar_para_busqueda(texto)
    tiene_pre_derivacion = (
        "HOJA DE PRE" in texto_normalizado
        or "PRE-DERIVACION" in texto_normalizado
        or "PRE DERIVACION" in texto_normalizado
        or "PREDERIVACION" in texto_normalizado
    )
    tiene_hoja_ruta = "HOJA DE RUTA" in texto_normalizado or "RUTA" in texto_normalizado
    tiene_codigo_ruta = re.search(r"\b[A-Z]\s*-\s*\d{4,}\s*-\s*\d{4}\b", texto_normalizado) is not None

    return tiene_pre_derivacion or (tiene_hoja_ruta and tiene_codigo_ruta)


def extraer_codigo_hoja_ruta(texto):
    match = re.search(r"\b([A-Z]\s*-\s*\d{4,}\s*-\s*\d{4})\b", texto, flags=re.IGNORECASE)
    if not match:
        return ""

    return limpiar_texto(match.group(1)).replace(" ", "")


def nombre_seguro(txt):
    txt = str(txt).strip()
    if not txt:
        return ""

    txt = txt.replace("/", "-")
    txt = txt.replace("\\", "-")
    txt = txt.replace(":", "-")
    txt = txt.replace("*", "")
    txt = txt.replace("?", "")
    txt = txt.replace('"', "")
    txt = txt.replace("<", "")
    txt = txt.replace(">", "")
    txt = txt.replace("|", "")
    txt = re.sub(r"\s+", "_", txt)
    txt = re.sub(r"_+", "_", txt)

    return txt.strip("_")


def obtener_ruta_salida_unica(output_dir, nombre_salida):
    output_dir = Path(output_dir)
    ruta = output_dir / nombre_salida
    if not ruta.exists():
        return ruta

    stem = ruta.stem
    suffix = ruta.suffix
    contador = 2

    while True:
        nueva_ruta = output_dir / f"{stem}_{contador}{suffix}"
        if not nueva_ruta.exists():
            return nueva_ruta
        contador += 1


def _guardar_json(data, path):
    path = Path(path)
    asegurar_directorio(path.parent)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _guardar_reporte_general(resultados, reportes):
    reportes = Path(reportes)
    asegurar_directorio(reportes)
    reporte_general = reportes / "reporte_general_separacion.csv"
    pd.DataFrame(resultados).to_csv(reporte_general, index=False, encoding="utf-8-sig")
    return reporte_general


def _resultado_pdf(item, estado, salida_pdf, reportes_pdf, hojas_detectadas, pdfs_generados, error):
    return {
        "caja": item["caja"],
        "carpeta": item["carpeta"],
        "pdf_origen": item["pdf_origen"],
        "ruta_pdf_origen": str(item["pdf_path"]),
        "estado": estado,
        "hojas_detectadas": hojas_detectadas,
        "pdfs_generados": pdfs_generados,
        "ruta_salida": str(salida_pdf),
        "ruta_reportes": str(reportes_pdf),
        "error": error,
    }
