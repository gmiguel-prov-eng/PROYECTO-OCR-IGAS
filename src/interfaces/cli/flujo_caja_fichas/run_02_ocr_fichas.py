import argparse
import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.flujo_caja_fichas.ocr_fichas import ejecutar
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(description="Ejecuta el proceso 2: OCR de fichas.")
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("02_ocr_fichas", config["paths"]["logs"])

    resumen = ejecutar(config, logger)
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()

