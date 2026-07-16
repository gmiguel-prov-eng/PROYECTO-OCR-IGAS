import argparse
import shutil
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from infrastructure.config.lotes import preparar_config_lote
from infrastructure.config.yaml_loader import cargar_config
from infrastructure.logging.logger import configurar_logger


def copiar_archivos(src_root, dst_root, extension="*.pdf"):
    src_root = Path(src_root)
    if not src_root.exists():
        return 0

    copied = 0
    for src_path in sorted(src_root.rglob(extension)):
        if not src_path.is_file():
            continue

        # Copiar todos los PDF a una sola carpeta plana sin subdirectorios.
        dst_path = dst_root / src_path.name
        if dst_path.exists():
            # Evitar colisiones de nombres usando un prefijo basado en la ruta relativa.
            relative = src_path.relative_to(src_root)
            safe_name = "_".join(relative.parts)
            dst_path = dst_root / safe_name
            index = 1
            while dst_path.exists():
                dst_path = dst_root / f"{safe_name}_{index}{src_path.suffix}"
                index += 1

        dst_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        copied += 1

    return copied


def copiar_archivo(src_path, dst_path):
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    if not src_path.exists() or not src_path.is_file():
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Exporta resultados de analisis y PDF de revision para un lote definido."
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument("--lote", required=True, type=int, help="Numero de lote a exportar.")
    parser.add_argument(
        "--tamano-lote",
        type=int,
        default=5,
        help="Cantidad de cajas por lote. Por defecto: 5.",
    )
    parser.add_argument(
        "--output-name",
        default="revisar",
        help="Nombre de la carpeta de salida dentro de output. Por defecto: revisar.",
    )
    parser.add_argument(
        "--revision-expedientes",
        action="store_true",
        help="Exporta los expedientes (PDFs) y el inventario_final en 'revision_expedientes'.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = preparar_config_lote(config, args.lote, args.tamano_lote)

    logger = configurar_logger(f"export_review_lote_{args.lote}", config["paths"]["logs"])
    logger.info("Exportando resultados de lote %s", args.lote)

    tablas = Path(config["paths"]["work"]["analisis"]["tablas"])
    revision_pdfs = Path(config["paths"]["work"]["analisis"]["pdfs_clasificados"]["revision"])
    rf = config["paths"]["output"]["resultados_finales"]
    output_base = Path(rf["inventario"]).parent
    revision_manual = config["paths"]["work"].get("revision_manual")

    # Revisión: work/03_1_revision_manual (no carpeta results).
    # --revision-expedientes: export auxiliar bajo 04_resultados_finales/revision_expedientes.
    if args.revision_expedientes:
        output_root = output_base / "revision_expedientes" / config["lote"]["nombre"]
    elif revision_manual:
        output_root = Path(revision_manual) / config["lote"]["nombre"]
    else:
        output_root = output_base / args.output_name / config["lote"]["nombre"]

    logger.info("Carpeta de salida: %s", output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    copied_items = []

    reporte_general_src = tablas / "reporte_general.csv"
    if copiar_archivo(reporte_general_src, output_root / "reporte_general.csv"):
        copied_items.append(str(reporte_general_src))
        logger.info("Copiado reporte_general.csv")
    else:
        logger.warning("No se encontro reporte_general.csv en %s", reporte_general_src)

    revision_csv_src = tablas / "revision.csv"
    if copiar_archivo(revision_csv_src, output_root / "revision.csv"):
        copied_items.append(str(revision_csv_src))
        logger.info("Copiado revision.csv")
    else:
        logger.warning("No se encontro revision.csv en %s", revision_csv_src)

    pdf_output_root = output_root / "pdfs_revision"
    pdf_count = copiar_archivos(revision_pdfs, pdf_output_root, extension="*.pdf")
    logger.info("Copiados %s PDFs de revision desde %s", pdf_count, revision_pdfs)

    # Si el usuario pide exportar expedientes, copiar PDFs de la carpeta 'expedientes'
    if args.revision_expedientes:
        expedientes_src = Path(config["paths"]["output"]["resultados_finales"]["expedientes"])
        expedientes_dst = output_root / "expedientes"
        expedientes_copied = copiar_archivos(expedientes_src, expedientes_dst, extension="*.pdf")
        logger.info("Copiados %s PDFs desde expedientes %s", expedientes_copied, expedientes_src)

        # Copiar inventario_final.csv si existe
        inventario_src = Path(config["paths"]["output"]["resultados_finales"]["inventario"]) / "inventario_final.csv"
        if copiar_archivo(inventario_src, output_root / "inventario_final.csv"):
            logger.info("Copiado inventario_final.csv desde %s", inventario_src)
        else:
            logger.warning("No se encontro inventario_final.csv en %s", inventario_src)

    logger.info("Exportacion finalizada. Archivos copiados: %s", len(copied_items) + pdf_count)
    print("Exportacion completa:")
    print(f"  Lote: {config['lote']['nombre']}")
    print(f"  Carpeta de salida: {output_root}")
    print(f"  CSV copiados: {len(copied_items)}")
    print(f"  PDFs copiados: {pdf_count}")


if __name__ == "__main__":
    main()
