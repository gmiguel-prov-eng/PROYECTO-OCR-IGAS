import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales import reporte_expedientes_subsanados
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Genera inventario de PDF en work/03_1_revision_manual: "
            "hoja_ruta = nombre del archivo, datos enriquecidos desde reporte_total.json."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--pdfs",
        help="Carpeta con PDF. Por defecto: paths.work.revision_manual (03_1_revision_manual).",
    )
    parser.add_argument(
        "--json",
        help="Ruta a reporte_total.json. Por defecto: work/02_ocr_fichas/reportes/reporte_total.json",
    )
    parser.add_argument(
        "--salida",
        help="Carpeta de salida del inventario. Por defecto: la misma de --pdfs.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("reporte_expedientes_subsanados", config["paths"]["logs"])

    resumen = reporte_expedientes_subsanados.ejecutar(
        config,
        logger,
        pdfs_dir=args.pdfs,
        reporte_json=args.json,
        salida_dir=args.salida,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
