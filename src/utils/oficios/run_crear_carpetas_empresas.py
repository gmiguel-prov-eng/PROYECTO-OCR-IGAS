import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger
from utils.oficios import crear_carpetas_empresas


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Crea subcarpetas en OFICIOS a partir de la pestaña EMPRESAS "
            "de seleccionados_total.xlsx (incluye COMPLETO/PARCIAL/INCOMPLETO)."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--xlsx",
        help="Ruta a seleccionados_total.xlsx. Por defecto: work/03_analisis/tablas/seleccionados_total.xlsx",
    )
    parser.add_argument(
        "--destino",
        help="Raiz OFICIOS. Por defecto: paths.input.oficios del YAML.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo lista las carpetas que se crearían.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("crear_carpetas_empresas", config["paths"]["logs"])

    resumen = crear_carpetas_empresas.ejecutar(
        config,
        logger,
        xlsx=args.xlsx,
        destino=args.destino,
        dry_run=args.dry_run,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
