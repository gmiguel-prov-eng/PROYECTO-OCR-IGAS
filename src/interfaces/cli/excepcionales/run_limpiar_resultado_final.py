import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales import limpiar_resultado_final
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Limpieza del resultado final (notebook REVISION_FINAL): "
            "enriquece con OCR fichas, quita remitente vacío, deduplica hoja_ruta "
            "y limpia asunto desde 'proyecto'. "
            "Tipos: oficios (reporte_solicitud_oficio) | expediente_final | ambos."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al YAML de configuracion.")
    parser.add_argument(
        "--tipo",
        choices=("oficios", "expediente_final", "ambos"),
        default="oficios",
        help="Que inventario limpiar. Por defecto: oficios.",
    )
    parser.add_argument("--entrada", help="CSV/XLSX de entrada (opcional).")
    parser.add_argument("--salida", help="CSV limpio de salida (opcional).")
    parser.add_argument(
        "--json",
        help="reporte_total.json. Por defecto: work/02_ocr_fichas/reportes/reporte_total.json",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("limpiar_resultado_final", config["paths"]["logs"])

    resumen = limpiar_resultado_final.ejecutar(
        config,
        logger,
        tipo=args.tipo,
        entrada=args.entrada,
        salida=args.salida,
        reporte_json=args.json,
    )
    print("Resumen de ejecucion:")
    if "tipos" in resumen:
        for item in resumen["tipos"]:
            print("---")
            for key, value in item.items():
                print(f"- {key}: {value}")
    else:
        for key, value in resumen.items():
            print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
