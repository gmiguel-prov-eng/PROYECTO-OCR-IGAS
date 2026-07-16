import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.flujo_caja_oficios.ocr_fichas_oficios import (
    descubrir_empresas,
    ejecutar,
    ejecutar_sincronizar_disco,
)
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta OCR de fichas de oficios por empresa. "
            "Actualiza la tabla general de forma acumulada: al correr otra empresa "
            "no se pierden filas ya procesadas. "
            "Con clasificacion, mueve PDF solo dentro de la carpeta de empresa."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--empresa",
        help="Nombre o fragmento de la carpeta de empresa a procesar. Ej: 'TELEFONICA' o 'AMERICA MOVIL'.",
    )
    parser.add_argument(
        "--excluir",
        help=(
            "Carpetas de empresa a omitir, separadas por coma. "
            "Ej: 'sin_clasificar,AMERICA MOVIL,otros'."
        ),
    )
    parser.add_argument(
        "--listar-empresas",
        action="store_true",
        help="Lista las carpetas de empresa detectadas en paths.input.oficios.",
    )
    parser.add_argument(
        "--sincronizar-disco",
        action="store_true",
        help=(
            "Sin OCR: alinea lista_fichas_oficios con PDF reales en "
            "COMPLETO|PARCIAL|INCOMPLETO (excluye carpeta otros)."
        ),
    )
    parser.add_argument(
        "--sin-clasificar",
        action="store_true",
        help="Solo OCR y CSV; no mueve PDF a COMPLETO|PARCIAL|INCOMPLETO dentro de la carpeta de empresa.",
    )
    parser.add_argument(
        "--solo-tabla",
        action="store_true",
        help=(
            "Solo regenera el CSV (OCR sobre PDF ya existentes, incluso en "
            "COMPLETO|PARCIAL|INCOMPLETO). No mueve archivos ni escribe ruta_pdf."
        ),
    )
    parser.add_argument(
        "--solo-parcial",
        action="store_true",
        help=(
            "2a pasada: OCR solo PDF en carpetas PARCIAL e INCOMPLETO (paginas 1 y 2). "
            "Conserva lista_fichas_oficios editada; actualiza solo si hay dato nuevo "
            "(no pisa hoja_ruta/revisado ya llenos; mejora conformidad a CUENTA)."
        ),
    )
    parser.add_argument(
        "--limite",
        type=int,
        help="Cantidad maxima de PDF por empresa (util para pruebas).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Muestra salida OCR de depuracion por PDF.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("05_ocr_fichas_oficios", config["paths"]["logs"])

    entrada = config["paths"]["input"]["oficios"]

    excluir = [x.strip() for x in (args.excluir or "").split(",") if x.strip()]

    if args.listar_empresas:
        empresas = descubrir_empresas(
            entrada,
            excluir=excluir or None,
            incluir_clasificados=True,
        )
        print(f"Empresas detectadas en {entrada}:")
        for nombre in empresas:
            print(f"  - {nombre}")
        return

    if args.sincronizar_disco:
        resumen = ejecutar_sincronizar_disco(config, logger, empresa=args.empresa)
    else:
        resumen = ejecutar(
            config,
            logger,
            empresa=args.empresa,
            excluir=excluir or None,
            clasificar=not args.sin_clasificar,
            limite=args.limite,
            debug=args.debug,
            solo_tabla=args.solo_tabla,
            solo_parcial=args.solo_parcial,
        )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
