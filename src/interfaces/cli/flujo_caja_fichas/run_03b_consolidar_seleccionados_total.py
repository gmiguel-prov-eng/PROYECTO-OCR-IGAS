import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.flujo_caja_fichas import consolidar_seleccionados_total
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Consolida seleccionados de lotes + OCR en seleccionados_total.xlsx "
            "(hojas SELECCIONADOS y EMPRESAS)."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--salida",
        help="Ruta del xlsx de salida. Por defecto: work/03_analisis/tablas/seleccionados_total.xlsx",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("consolidar_seleccionados_total", config["paths"]["logs"])

    resumen = consolidar_seleccionados_total.ejecutar(config, logger, salida=args.salida)
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
