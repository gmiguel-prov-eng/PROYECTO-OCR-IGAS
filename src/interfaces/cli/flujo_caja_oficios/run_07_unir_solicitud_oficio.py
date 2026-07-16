import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.flujo_caja_oficios.unir_solicitud_oficio import ejecutar
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Crea PDF NUEVOS uniendo solicitud + oficio (match_oficio=True). "
            "No mueve ni modifica los PDF de origen. "
            "Los PDF unidos se guardan en paths.output...expedientes_oficio_pdfs "
            "(EVAP-FICHAS TECNICAS/extension). "
            "El reporte CSV se acumula en expedientes_oficio (local)."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="Regenera PDF/reporte aunque la hoja_ruta ya exista en expedientes_oficio.",
    )
    parser.add_argument(
        "--empresa",
        help="Filtra por empresa (fragmento o lista separada por comas).",
    )
    parser.add_argument(
        "--salida-pdfs",
        help="Carpeta de PDF unidos (p. ej. extension/telefonica_entel_reproceso).",
    )
    parser.add_argument(
        "--reporte",
        help="CSV de reporte (nuevo/aislado). Si se usa, se omiten hoja_ruta del reporte principal.",
    )
    parser.add_argument(
        "--omitir-hojas-de",
        help="CSV de referencia cuyas hoja_ruta no se vuelven a unir (override del default).",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("unir_solicitud_oficio", config["paths"]["logs"])

    empresas = [x.strip() for x in (args.empresa or "").split(",") if x.strip()] or None

    resumen = ejecutar(
        config,
        logger,
        forzar=args.forzar,
        empresas=empresas,
        pdfs_dir=args.salida_pdfs,
        reporte=args.reporte,
        omitir_hojas_de=args.omitir_hojas_de,
    )
    print("Resumen de ejecucion:")
    for key, value in resumen.items():
        print(f"- {key}: {value}")


if __name__ == "__main__":
    main()
