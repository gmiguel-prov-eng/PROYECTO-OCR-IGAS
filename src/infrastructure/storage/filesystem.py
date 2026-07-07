from pathlib import Path


def asegurar_directorio(path):
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def listar_archivos(path, patron="*"):
    directory = Path(path)
    if not directory.exists():
        return []

    return sorted(item for item in directory.rglob(patron) if item.is_file())


def listar_pdfs(path):
    return listar_archivos(path, "*.pdf")

