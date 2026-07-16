import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from infrastructure.config.lotes import preparar_config_lote
from infrastructure.config.yaml_loader import cargar_config
from infrastructure.logging.logger import configurar_logger


def cargar_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def guardar_json(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def unir_json_en_carpeta(reportes_root, destino):
    reportes_root = Path(reportes_root)
    archivos = sorted(reportes_root.rglob("*.json"))
    datos_combinados = []

    for archivo in archivos:
        if archivo.name == destino.name:
            continue

        try:
            contenido = cargar_json(archivo)
        except Exception:
            continue

        if isinstance(contenido, list):
            datos_combinados.extend(contenido)
        else:
            datos_combinados.append(contenido)

    guardar_json(datos_combinados, destino)
    return len(archivos), len(datos_combinados)


def main():
    parser = argparse.ArgumentParser(
        description="Unir todos los JSON de OCR fichas en la carpeta reportes en un solo reporte_total.json."
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument("--lote", type=int, help="Numero de lote, si se desea usar configuracion de lote.")
    parser.add_argument(
        "--tamano-lote",
        type=int,
        default=5,
        help="Tamano de lote si se usa --lote. Por defecto: 5.",
    )
    parser.add_argument(
        "--output-name",
        default="reporte_total.json",
        help="Nombre del archivo de salida dentro de reportes. Por defecto: reporte_total.json.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    if args.lote:
        config = preparar_config_lote(config, args.lote, args.tamano_lote)

    logger = configurar_logger("merge_ocr_fichas_json", config["paths"]["logs"])
    reportes_root = Path(config["paths"]["work"]["ocr_fichas"]["reportes"])
    destino = reportes_root / args.output_name

    logger.info("Unificando JSON en %s", reportes_root)
    archivos_leidos, registros = unir_json_en_carpeta(reportes_root, destino)
    logger.info("JSON combinado guardado en %s", destino)
    print("Resultado:")
    print(f"  Carpeta reportes: {reportes_root}")
    print(f"  Archivos JSON leidos: {archivos_leidos}")
    print(f"  Registros totales en {args.output_name}: {registros}")


if __name__ == "__main__":
    main()
