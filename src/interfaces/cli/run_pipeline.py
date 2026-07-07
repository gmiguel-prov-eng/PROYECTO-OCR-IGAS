import argparse
import sys
import time
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases import analizar_datos, ocr_fichas, ocr_solicitudes, separar_pdf
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


PASOS_PIPELINE = [
    ("01", "separacion_pdf", separar_pdf.ejecutar),
    ("02", "ocr_fichas", ocr_fichas.ejecutar),
    ("03", "analisis_datos", analizar_datos.ejecutar),
    ("04", "ocr_solicitudes", ocr_solicitudes.ejecutar),
]


def main():
    parser = argparse.ArgumentParser(description="Ejecuta el pipeline documental completo.")
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--desde",
        choices=[codigo for codigo, _, _ in PASOS_PIPELINE],
        default="01",
        help="Codigo del primer paso a ejecutar. Por defecto: 01.",
    )
    parser.add_argument(
        "--hasta",
        choices=[codigo for codigo, _, _ in PASOS_PIPELINE],
        default="04",
        help="Codigo del ultimo paso a ejecutar. Por defecto: 04.",
    )
    parser.add_argument(
        "--continuar-con-errores",
        action="store_true",
        help="Continua con pasos posteriores aunque un paso reporte estado con_errores.",
    )
    args = parser.parse_args()

    if int(args.desde) > int(args.hasta):
        raise ValueError("--desde no puede ser mayor que --hasta.")

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("pipeline", config["paths"]["logs"])

    pasos = seleccionar_pasos(args.desde, args.hasta)
    resultados = ejecutar_pipeline(config, logger, pasos, continuar_con_errores=args.continuar_con_errores)

    imprimir_resumen(resultados)


def seleccionar_pasos(desde, hasta):
    return [
        paso
        for paso in PASOS_PIPELINE
        if int(desde) <= int(paso[0]) <= int(hasta)
    ]


def ejecutar_pipeline(config, logger, pasos, continuar_con_errores=False):
    resultados = []

    for codigo, nombre, funcion in pasos:
        logger.info("Iniciando paso %s - %s", codigo, nombre)
        inicio = time.perf_counter()

        try:
            resultado = funcion(config, logger)
            resultado["codigo_paso"] = codigo
            resultado["nombre_paso"] = nombre
            resultado["tiempo_seg"] = round(time.perf_counter() - inicio, 2)
        except Exception as exc:
            logger.exception("Error ejecutando paso %s - %s", codigo, nombre)
            resultado = {
                "codigo_paso": codigo,
                "nombre_paso": nombre,
                "proceso": nombre,
                "estado": "error",
                "error": str(exc),
                "tiempo_seg": round(time.perf_counter() - inicio, 2),
            }

        resultados.append(resultado)
        logger.info(
            "Paso %s - %s finalizado con estado=%s en %s segundos",
            codigo,
            nombre,
            resultado.get("estado"),
            resultado.get("tiempo_seg"),
        )

        if resultado.get("estado") in {"error", "con_errores"} and not continuar_con_errores:
            logger.warning("Pipeline detenido en paso %s por estado %s.", codigo, resultado.get("estado"))
            break

    return resultados


def imprimir_resumen(resultados):
    print("Resumen de pipeline:")
    for resultado in resultados:
        print(
            f"- {resultado['codigo_paso']} {resultado['nombre_paso']}: "
            f"{resultado.get('estado')} ({resultado.get('tiempo_seg')} s)"
        )

        for clave in claves_resumen(resultado):
            print(f"  {clave}: {resultado[clave]}")


def claves_resumen(resultado):
    preferidas = [
        "pdfs_detectados",
        "pdfs_procesados",
        "grupos_detectados",
        "registros",
        "seleccionados",
        "revision",
        "no_seleccionados",
        "no_considerados",
        "pdfs_con_informe_tecnico",
        "pdfs_sin_informe_tecnico",
        "expedientes_copiados",
        "reporte_general",
        "ocr_languages",
        "ocr_missing_languages",
        "error",
    ]
    return [clave for clave in preferidas if clave in resultado]


if __name__ == "__main__":
    main()
