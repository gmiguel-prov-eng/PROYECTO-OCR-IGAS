import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales import reporte_final
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Genera reporte_final.xlsx con 2 pestañas: inventario_final y "
            "solicitudes_sueltas."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al YAML de configuracion.")
    parser.add_argument(
        "--inventario",
        help="Tabla de inventario final. Por defecto: 04_resultados_finales/inventario_lote_2.xlsx",
    )
    parser.add_argument(
        "--sueltas",
        help="CSV de solicitudes sueltas. Por defecto: work/05_fichas_oficios/reportes/solicitudes_sueltas.csv",
    )
    parser.add_argument(
        "--salida",
        help="Excel de salida. Por defecto: 04_resultados_finales/reporte_final.xlsx",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("reporte_final", config["paths"]["logs"])

    resumen = reporte_final.ejecutar(
        config,
        logger,
        inventario=args.inventario,
        sueltas=args.sueltas,
        salida=args.salida,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
