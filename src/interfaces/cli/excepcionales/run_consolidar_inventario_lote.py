import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales import consolidar_inventario_lote
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Une inventario_final_alimentado_limpio + inventario_subsanados + "
            "reporte_solicitud_oficio_limpio → Excel inventario_lote_2.xlsx "
            "(proyecto_url en blanco)."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al YAML de configuracion.")
    parser.add_argument(
        "--salida",
        help="Excel de salida. Por defecto: 04_resultados_finales/inventario_lote_2.xlsx",
    )
    parser.add_argument("--hoja", default="INVENTARIO", help="Nombre de la hoja Excel.")
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("consolidar_inventario_lote", config["paths"]["logs"])

    resumen = consolidar_inventario_lote.ejecutar(
        config,
        logger,
        salida=args.salida,
        nombre_hoja=args.hoja,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
