import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.excepcionales.separar_pdf_oficios import (
    _resolver_entrada_oficio_unido,
    descubrir_pdfs_oficios,
    ejecutar,
)
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Separa PDF unidos de oficios (paths.input.oficio_unido). "
            "Detecta 'OFICIO N° ...' y guarda cada documento unico en "
            "01_separacion/separados_oficio nombrado por numero de oficio o hoja_ruta."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--empresa",
        help="Filtra por carpeta/empresa o fragmento del nombre. Ej: 'ENTEL', 'FIBERTEL'.",
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="Lista PDF candidatos en paths.input.oficio_unido sin procesar.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        help="Maximo de PDF a procesar (pruebas).",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("separar_pdf_oficios", config["paths"]["logs"])

    entrada = _resolver_entrada_oficio_unido(config["paths"]["input"])

    if args.listar:
        pdfs = descubrir_pdfs_oficios(entrada, filtro_empresa=args.empresa, limite=args.limite)
        print(f"PDF candidatos en oficio_unido ({entrada}): {len(pdfs)}")
        for item in pdfs[:50]:
            print(f"  - [{item['empresa']}] {item['pdf_path'].name}")
        if len(pdfs) > 50:
            print(f"  ... y {len(pdfs) - 50} mas")
        return

    resumen = ejecutar(config, logger, empresa=args.empresa, limite=args.limite)
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
