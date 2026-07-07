from copy import deepcopy
from pathlib import Path

import yaml


def cargar_config(path_config):
    """Carga un archivo YAML de configuracion."""
    config_path = Path(path_config).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"No existe el archivo de configuracion: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    if "paths" not in config:
        raise KeyError("El archivo de configuracion debe incluir la seccion 'paths'.")

    return config


def resolver_rutas(config):
    """Convierte las rutas definidas en YAML a objetos pathlib.Path."""
    resolved_config = deepcopy(config)
    resolved_config["paths"] = _resolver_nodo_rutas(resolved_config.get("paths", {}))
    return resolved_config


def crear_directorios_base(config):
    """Crea todos los directorios definidos en la seccion paths del YAML."""
    resolved_config = resolver_rutas(config)

    for path in iterar_rutas(resolved_config["paths"]):
        path.mkdir(parents=True, exist_ok=True)

    return resolved_config


def iterar_rutas(node):
    """Itera recursivamente sobre las hojas Path de un diccionario de rutas."""
    if isinstance(node, Path):
        yield node
        return

    if isinstance(node, dict):
        for value in node.values():
            yield from iterar_rutas(value)


def _resolver_nodo_rutas(node):
    if isinstance(node, dict):
        return {key: _resolver_nodo_rutas(value) for key, value in node.items()}

    if isinstance(node, str):
        return Path(node).expanduser()

    return node

