import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales import merge_expediente_final
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Limpieza final ETL: consolida PDF e inventarios de lotes en "
            "paths.output.resultados_finales.expediente_final."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument("--desde", type=int, default=1, help="Lote inicial. Por defecto: 1.")
    parser.add_argument("--hasta", type=int, default=31, help="Lote final. Por defecto: 31.")
    parser.add_argument(
        "--sin-limpiar-pdfs",
        action="store_true",
        help="No vacía expediente_final/pdfs antes de copiar.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("merge_expediente_final", config["paths"]["logs"])

    resumen = merge_expediente_final.ejecutar(
        config,
        logger,
        desde=args.desde,
        hasta=args.hasta,
        limpiar_pdfs=not args.sin_limpiar_pdfs,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
