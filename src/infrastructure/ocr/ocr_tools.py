import subprocess

import pytesseract


def texto_no_vacio(value):
    if value is None:
        return False

    return bool(str(value).strip())


def configurar_tesseract(config_ocr):
    tesseract_cmd = config_ocr.get("tesseract_cmd")
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_cmd)


def obtener_idiomas_instalados(config_ocr):
    tesseract_cmd = config_ocr.get("tesseract_cmd") or "tesseract"
    result = subprocess.run(
        [str(tesseract_cmd), "--list-langs"],
        check=True,
        capture_output=True,
        text=True,
    )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return set(lines[1:])


def separar_idiomas(language_spec):
    return {language.strip() for language in str(language_spec).split("+") if language.strip()}


def validar_idiomas_tesseract(config_ocr, logger=None):
    configurar_tesseract(config_ocr)

    requeridos = separar_idiomas(config_ocr.get("languages", "spa+eng"))
    fallback = config_ocr.get("fallback_languages")
    require_languages = bool(config_ocr.get("require_languages", False))
    instalados = obtener_idiomas_instalados(config_ocr)
    faltantes = requeridos - instalados

    resultado = {
        "requested_languages": "+".join(sorted(requeridos)),
        "effective_languages": config_ocr.get("languages", "spa+eng"),
        "installed_languages": sorted(instalados),
        "missing_languages": sorted(faltantes),
        "using_fallback": False,
    }

    if not faltantes:
        if logger:
            logger.info("Idiomas Tesseract disponibles: %s", ", ".join(sorted(instalados)))
        return resultado

    mensaje = (
        "Faltan idiomas de Tesseract: "
        f"{', '.join(sorted(faltantes))}. Instalados: {', '.join(sorted(instalados))}."
    )

    if require_languages or not fallback:
        raise RuntimeError(mensaje)

    fallback_requeridos = separar_idiomas(fallback)
    fallback_faltantes = fallback_requeridos - instalados
    if fallback_faltantes:
        raise RuntimeError(
            mensaje
            + " Ademas faltan idiomas fallback: "
            + ", ".join(sorted(fallback_faltantes))
            + "."
        )

    config_ocr["languages"] = fallback
    resultado["effective_languages"] = fallback
    resultado["using_fallback"] = True

    if logger:
        logger.warning("%s Se usara fallback OCR: %s", mensaje, fallback)

    return resultado
