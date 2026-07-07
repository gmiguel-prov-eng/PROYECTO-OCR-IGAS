from pathlib import Path


def nombre_base_pdf(path_pdf):
    return Path(path_pdf).stem


def es_pdf(path):
    return Path(path).suffix.lower() == ".pdf"

