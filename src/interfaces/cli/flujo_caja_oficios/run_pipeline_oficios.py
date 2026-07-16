import argparse
import sys
import time
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.use_cases.flujo_caja_oficios import (
    completar_solicitud,
    ocr_fichas_oficios,
    unir_solicitud_oficio,
)
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base
from infrastructure.logging.logger import configurar_logger


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Pipeline solicitud-oficio por empresa (sin revision manual): "
            "05 OCR/clasificacion oficios -> "
            "06 match solicitud_oficio -> "
            "07 crear PDF unidos nuevos."
        )
    )
    parser.add_argument("--config", required=True, help="Ruta al archivo YAML de configuracion.")
    parser.add_argument(
        "--empresa",
        help="Nombre o fragmento de la carpeta de empresa. Ej: 'FIBERTEL', 'TELEFONICA'.",
    )
    parser.add_argument(
        "--listar-empresas",
        action="store_true",
        help="Lista las carpetas de empresa detectadas en paths.input.oficios.",
    )
    parser.add_argument(
        "--solo-tabla",
        action="store_true",
        help="En el OCR, regenera la tabla sin mover PDF (lee tambien COMPLETO/PARCIAL/INCOMPLETO).",
    )
    parser.add_argument(
        "--sin-clasificar",
        action="store_true",
        help="En el OCR, no mueve PDF a COMPLETO/PARCIAL/INCOMPLETO.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        help="Cantidad maxima de PDF por empresa en el OCR (pruebas).",
    )
    parser.add_argument(
        "--forzar-union",
        action="store_true",
        help="Regenera PDF unidos aunque la hoja_ruta ya exista en el reporte.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Salida OCR de depuracion por PDF.",
    )
    args = parser.parse_args()

    config = cargar_config(args.config)
    config = crear_directorios_base(config)
    logger = configurar_logger("pipeline_oficios", config["paths"]["logs"])

    entrada = config["paths"]["input"]["oficios"]
    if args.listar_empresas:
        empresas = ocr_fichas_oficios.descubrir_empresas(entrada, incluir_clasificados=True)
        print(f"Empresas detectadas en {entrada}:")
        for nombre in empresas:
            print(f"  - {nombre}")
        return

    if not args.empresa:
        parser.error("Indica --empresa o usa --listar-empresas.")

    resultados = []

    # Paso 05: OCR + clasificacion (acumulado por empresa)
    resultados.append(
        _correr_paso(
            logger,
            codigo="05",
            nombre="ocr_fichas_oficios",
            funcion=lambda: ocr_fichas_oficios.ejecutar(
                config,
                logger,
                empresa=args.empresa,
                clasificar=not args.sin_clasificar,
                limite=args.limite,
                debug=args.debug,
                solo_tabla=args.solo_tabla,
            ),
        )
    )
    if _detener(resultados[-1]):
        _imprimir_resumen(resultados)
        return

    # Paso 06: match solicitud <-> oficio
    resultados.append(
        _correr_paso(
            logger,
            codigo="06",
            nombre="completar_solicitud",
            funcion=lambda: completar_solicitud.ejecutar(config, logger),
        )
    )
    if _detener(resultados[-1]):
        _imprimir_resumen(resultados)
        return

    # Paso 07: crear PDF nuevos unidos
    resultados.append(
        _correr_paso(
            logger,
            codigo="07",
            nombre="unir_solicitud_oficio",
            funcion=lambda: unir_solicitud_oficio.ejecutar(
                config,
                logger,
                forzar=args.forzar_union,
            ),
        )
    )

    _imprimir_resumen(resultados)


def _correr_paso(logger, codigo, nombre, funcion):
    logger.info("Iniciando paso %s - %s", codigo, nombre)
    inicio = time.perf_counter()
    try:
        resultado = funcion()
        if not isinstance(resultado, dict):
            resultado = {"estado": "completado"}
        resultado.setdefault("estado", "completado")
    except Exception as exc:
        logger.exception("Error en paso %s - %s", codigo, nombre)
        resultado = {
            "estado": "error",
            "error": str(exc),
        }

    resultado["codigo_paso"] = codigo
    resultado["nombre_paso"] = nombre
    resultado["tiempo_seg"] = round(time.perf_counter() - inicio, 2)
    logger.info(
        "Paso %s - %s finalizado: estado=%s (%s s)",
        codigo,
        nombre,
        resultado.get("estado"),
        resultado.get("tiempo_seg"),
    )
    return resultado


def _detener(resultado):
    return resultado.get("estado") in {"error", "con_errores"}


def _imprimir_resumen(resultados):
    print("Resumen pipeline oficios:")
    for resultado in resultados:
        print(
            f"- {resultado['codigo_paso']} {resultado['nombre_paso']}: "
            f"{resultado.get('estado')} ({resultado.get('tiempo_seg')} s)"
        )
        for clave in (
            "empresa_filtro",
            "empresas_procesadas",
            "pdfs_procesados",
            "pdfs_en_tabla_general",
            "oficios_usados",
            "con_match_oficio",
            "pdfs_creados_esta_corrida",
            "omitidos_ya_existentes",
            "filas_reporte_total",
            "csv_general",
            "salida",
            "reporte",
            "salida_pdfs",
            "error",
        ):
            if clave in resultado:
                print(f"  {clave}: {resultado[clave]}")


if __name__ == "__main__":
    main()
