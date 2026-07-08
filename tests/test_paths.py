import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
CONFIG_TEST = PROJECT_ROOT / "config" / "config_example.yaml"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from infrastructure.config.lotes import preparar_config_lote
from infrastructure.config.yaml_loader import cargar_config, crear_directorios_base, iterar_rutas, resolver_rutas
from interfaces.cli.run_pipeline import PASOS_PIPELINE, seleccionar_pasos


def test_carga_config_local():
    config = cargar_config(CONFIG_TEST)

    assert config["project"]["environment"] == "example"
    assert "paths" in config
    assert "input" in config["paths"]


def test_creacion_de_rutas_principales():
    config = cargar_config(CONFIG_TEST)
    config = crear_directorios_base(config)

    rutas = list(iterar_rutas(config["paths"]))
    assert rutas
    assert all(path.exists() for path in rutas)


def test_rutas_resueltas_usan_pathlib():
    config = cargar_config(CONFIG_TEST)
    config = resolver_rutas(config)

    rutas = list(iterar_rutas(config["paths"]))
    assert rutas
    assert all(isinstance(path, Path) for path in rutas)


def test_rutas_principales_estan_definidas_en_yaml():
    config = cargar_config(CONFIG_TEST)
    paths = config["paths"]

    assert paths["input"]["cajas"]
    assert paths["work"]["separacion"]["separados"]
    assert paths["work"]["separacion"]["reportes"]
    assert paths["work"]["ocr_fichas"]["extraido"]
    assert paths["work"]["ocr_fichas"]["reportes"]
    assert paths["work"]["analisis"]["tablas"]
    assert paths["work"]["analisis"]["pdfs_clasificados"]["seleccionados"]
    assert paths["work"]["analisis"]["pdfs_clasificados"]["revision"]
    assert paths["work"]["analisis"]["pdfs_clasificados"]["no_seleccionados"]
    assert paths["work"]["analisis"]["pdfs_clasificados"]["no_considerados"]
    assert paths["work"]["ocr_solicitudes"]["extraido"]
    assert paths["work"]["ocr_solicitudes"]["reportes"]
    assert paths["output"]["resultados_finales"]["resultados"]
    assert paths["output"]["resultados_finales"]["expedientes"]
    assert paths["output"]["resultados_finales"]["inventario"]
    assert paths["logs"]


def test_config_ocr_esta_definida():
    config = cargar_config(CONFIG_TEST)

    assert config["ocr"]["tesseract_cmd"]
    assert config["ocr"]["languages"]


def test_pipeline_tiene_cuatro_pasos_en_orden():
    codigos = [codigo for codigo, _, _ in PASOS_PIPELINE]
    nombres = [nombre for _, nombre, _ in PASOS_PIPELINE]

    assert codigos == ["01", "02", "03", "04"]
    assert nombres == ["separacion_pdf", "ocr_fichas", "analisis_datos", "ocr_solicitudes"]


def test_pipeline_permite_seleccionar_rango_de_pasos():
    pasos = seleccionar_pasos("02", "03")
    codigos = [codigo for codigo, _, _ in pasos]

    assert codigos == ["02", "03"]


def test_config_lote_filtra_cajas_y_separa_salidas(tmp_path):
    input_cajas = tmp_path / "input" / "cajas"
    for nombre in ["caja_1", "caja_2", "caja_3", "caja_4", "caja_5", "caja_6", "caja_10"]:
        (input_cajas / nombre).mkdir(parents=True)

    config = cargar_config(CONFIG_TEST)
    config["paths"]["input"]["cajas"] = input_cajas
    config["paths"]["work"]["separacion"]["separados"] = tmp_path / "work" / "separados"
    config["paths"]["work"]["separacion"]["reportes"] = tmp_path / "work" / "reportes"
    config["paths"]["output"]["resultados_finales"]["resultados"] = tmp_path / "output" / "resultados"
    config["paths"]["logs"] = tmp_path / "logs"

    config_lote = preparar_config_lote(config, numero_lote=2, tamano_lote=5)

    assert config_lote["lote"]["nombre"] == "lote_2"
    assert config_lote["lote"]["cajas"] == ["caja_6", "caja_10"]
    assert config_lote["paths"]["input"]["cajas"] == input_cajas
    assert config_lote["paths"]["work"]["separacion"]["separados"].name == "lote_2"
    assert config_lote["paths"]["output"]["resultados_finales"]["resultados"].name == "lote_2"
    assert config_lote["paths"]["logs"].name == "lote_2"


def test_no_hay_rutas_absolutas_quemadas_en_src():
    patrones_prohibidos = ("D:/", "D:\\", "/mnt/oficina")
    archivos_python = SRC_ROOT.rglob("*.py")

    for archivo in archivos_python:
        contenido = archivo.read_text(encoding="utf-8")
        assert not any(patron in contenido for patron in patrones_prohibidos), archivo
