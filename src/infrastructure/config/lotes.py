import re
from copy import deepcopy
from math import ceil
from pathlib import Path


def preparar_config_lote(config, numero_lote, tamano_lote=5):
    """Ajusta la configuracion para procesar un lote de cajas."""
    numero_lote = int(numero_lote)
    tamano_lote = int(tamano_lote)
    if numero_lote < 1:
        raise ValueError("El numero de lote debe ser mayor o igual a 1.")
    if tamano_lote < 1:
        raise ValueError("El tamano de lote debe ser mayor o igual a 1.")

    lote_nombre = f"lote_{numero_lote}"
    config_lote = deepcopy(config)

    cajas = descubrir_cajas(config_lote["paths"]["input"]["cajas"])
    total_lotes = ceil(len(cajas) / tamano_lote) if cajas else 0
    inicio = (numero_lote - 1) * tamano_lote
    fin = inicio + tamano_lote
    cajas_lote = cajas[inicio:fin]

    if not cajas_lote:
        raise ValueError(
            f"No hay cajas para {lote_nombre}. "
            f"Cajas detectadas: {len(cajas)}. Tamano de lote: {tamano_lote}."
        )

    config_lote["lote"] = {
        "nombre": lote_nombre,
        "numero": numero_lote,
        "tamano_cajas": tamano_lote,
        "total_lotes": total_lotes,
        "cajas": cajas_lote,
    }
    config_lote["paths"] = agregar_lote_a_salidas(config_lote["paths"], lote_nombre)
    return config_lote


def descubrir_cajas(input_cajas):
    input_cajas = Path(input_cajas).expanduser()
    if not input_cajas.exists():
        return []

    return sorted(
        (item.name for item in input_cajas.iterdir() if item.is_dir()),
        key=_clave_orden_natural,
    )


def agregar_lote_a_salidas(paths, lote_nombre):
    paths_lote = deepcopy(paths)

    if "work" in paths_lote:
        paths_lote["work"] = _agregar_lote_a_hojas(paths_lote["work"], lote_nombre)
    if "output" in paths_lote:
        paths_lote["output"] = _agregar_lote_a_hojas(paths_lote["output"], lote_nombre)
    if "logs" in paths_lote:
        paths_lote["logs"] = Path(paths_lote["logs"]) / lote_nombre

    return paths_lote


def _agregar_lote_a_hojas(node, lote_nombre):
    if isinstance(node, dict):
        return {
            key: _agregar_lote_a_hojas(value, lote_nombre)
            for key, value in node.items()
        }

    return Path(node) / lote_nombre


def _clave_orden_natural(texto):
    partes = re.split(r"(\d+)", str(texto).lower())
    return [int(parte) if parte.isdigit() else parte for parte in partes]
