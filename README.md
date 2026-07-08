# PROYECTO-IGAS

Proyecto OCR/documental para procesar expedientes PDF del MTC.

El proyecto conserva los notebooks originales como respaldo, pero el flujo principal ya puede ejecutarse desde scripts Python con rutas centralizadas en YAML.

## Flujo de trabajo

1. Separacion de PDFs originales por caja, carpeta y PDF origen.
2. OCR de fichas sobre los PDFs separados.
3. Analisis tabular y clasificacion de PDFs.
4. OCR de solicitudes sobre PDFs seleccionados e inventario final.

Los notebooks originales deben conservarse como respaldo dentro de `notebooks/`.

## Estructura

```text
config/
data/
  input/
  work/
  output/
  logs/
notebooks/
src/
  application/use_cases/
  infrastructure/
  interfaces/cli/
tests/
```

`data/input/` contiene insumos originales.

`data/work/` contiene salidas intermedias de cada proceso.

`data/output/` contiene resultados finales.

`data/logs/` contiene logs de ejecucion.

## Configuracion

Las rutas se centralizan en:

- `config/config_example.yaml`: plantilla versionable para el repositorio.
- `config/config_local.yaml`: laptop Windows, no versionar.
- `config/config_vm.yaml`: maquina virtual Linux Red Hat, no versionar.

Las rutas absolutas solo deben vivir en archivos YAML de configuracion local o de VM. Dentro de `src/` se deben usar rutas resueltas desde YAML con `pathlib`.

Despues de clonar el repositorio, crea tu config local desde la plantilla:

```bash
copy config\config_example.yaml config\config_local.yaml
```

En Linux:

```bash
cp config/config_example.yaml config/config_vm.yaml
```

Luego ajusta rutas y `ocr.tesseract_cmd` segun el entorno.

## Tesseract OCR

`pytesseract` es una libreria de Python, pero el motor real de OCR es Tesseract instalado en el sistema operativo.

En `config/config_local.yaml` y `config/config_vm.yaml` se define:

- `ocr.tesseract_cmd`: ubicacion del ejecutable de Tesseract.
- `ocr.languages`: idiomas deseados para OCR.
- `ocr.fallback_languages`: idioma alternativo si falta alguno en local.
- `ocr.require_languages`: si debe fallar cuando falte un idioma.

Para este proyecto, el idioma recomendado es `spa+eng`, porque los expedientes estan en espanol. Si falta `spa`, el proceso lo reporta en el resumen y en logs. En Windows local puede usarse `eng` como fallback temporal para pruebas, pero para OCR real de fichas y solicitudes debe instalarse el paquete de idioma espanol de Tesseract.

Validacion manual:

```bash
"C:/Program Files/Tesseract-OCR/tesseract.exe" --list-langs
```

En la VM:

```bash
tesseract --list-langs
```

## Ejecucion local

Proceso por proceso:

```bash
python src/interfaces/cli/run_01_separar_pdf.py --config config/config_local.yaml
python src/interfaces/cli/run_02_ocr_fichas.py --config config/config_local.yaml
python src/interfaces/cli/run_03_analisis_datos.py --config config/config_local.yaml
python src/interfaces/cli/run_04_ocr_solicitudes.py --config config/config_local.yaml
```

Pipeline completo:

```bash
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml
```

Procesamiento por lotes de 5 cajas:

```bash
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --lote 1
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --lote 2
```

Cada lote procesa 5 carpetas de caja, ordenadas alfabeticamente desde `paths.input.cajas`.
Las salidas se guardan separadas agregando `lote_N` a las rutas de `work`, `output` y `logs`,
por ejemplo `data/work/02_ocr_fichas/extraido/lote_1`.

Para cambiar la cantidad de cajas por lote:

```bash
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --lote 1 --tamano-lote 5
```

El pipeline tambien permite ejecutar rangos de pasos:

```bash
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --desde 02 --hasta 04
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --desde 03 --hasta 03
```

Por defecto, el pipeline se detiene si un paso reporta `error` o `con_errores`. Para continuar aun con errores:

```bash
python src/interfaces/cli/run_pipeline.py --config config/config_local.yaml --continuar-con-errores
```

## Ejecucion en VM

```bash
python src/interfaces/cli/run_01_separar_pdf.py --config config/config_vm.yaml
python src/interfaces/cli/run_pipeline.py --config config/config_vm.yaml
```

## Reglas de rutas

- Usar `pathlib` para todas las rutas.
- No quemar rutas absolutas dentro de `src/`.
- Cambiar de entorno modificando el YAML usado con `--config`.
- Mantener trazabilidad: caja, carpeta y PDF origen deben conservarse en las salidas.
- No versionar `config/config_local.yaml` ni `config/config_vm.yaml`.
- Versionar solo `config/config_example.yaml`.

## Control de Versiones

No deben subirse al repositorio:

- PDFs originales o separados.
- CSV, XLSX o reportes generados.
- Carpetas `data/input/`, `data/work/`, `data/output/` y `data/logs/`.
- Entornos virtuales como `proyecto_igas/`, `.venv/`, `venv/` o `env/`.
- Notebooks originales dentro de `notebooks/`, salvo `notebooks/README.md`.
- Configuraciones reales con rutas de laptop o VM.

El `.gitignore` ya cubre esos casos. Antes de subir, revisa:

```bash
git status --short
```

Si aparece algo bajo `data/`, `proyecto_igas/` o configs reales, no lo agregues al commit.

## Salidas principales

Proceso 1:

- `data/work/01_separacion/separados/`
- `data/work/01_separacion/reportes/reporte_general_separacion.csv`

Proceso 2:

- `data/work/02_ocr_fichas/extraido/`
- `data/work/02_ocr_fichas/reportes/reporte_general_ocr_fichas.csv`

Proceso 3:

- `data/work/03_analisis/tablas/reporte_general.csv`
- `data/work/03_analisis/tablas/seleccionados.csv`
- `data/work/03_analisis/tablas/revision.csv`
- `data/work/03_analisis/tablas/no_seleccionados.csv`
- `data/work/03_analisis/pdfs_clasificados/`

El `reporte_general.csv` del proceso 3 incluye `carpeta_destino`, que controla a que carpeta se distribuye cada PDF.

Proceso 4:

- `data/output/04_resultados_finales/resultados/resultados_ocr_solicitudes.csv`
- `data/output/04_resultados_finales/inventario/inventario_general.csv`
- `data/output/04_resultados_finales/inventario/inventario_final.csv`
- `data/output/04_resultados_finales/expedientes/`

En el proceso 4, `tiene_informe = True` solo cuando se detecta explicitamente `INFORME TECNICO`.

## Pruebas

Las pruebas basicas estan en `tests/`.

Para instalar dependencias de desarrollo:

```bash
pip install -r requirements-dev.txt
```

Para ejecutar:

```bash
pytest tests
```

## Estrategia de migracion

Etapa 1:

- Crear estructura.
- Centralizar rutas.
- Crear runners CLI.
- Crear use cases base.
- Crear logger y pruebas basicas.

Etapa 2:

- Limpiar notebooks solo en manejo de rutas.
- Reemplazar rutas quemadas por rutas leidas desde YAML.
- Mantener la logica principal intacta.

Etapa 3:

- Migrar logica de notebooks a `src/application/use_cases/`.
- Mantener ejecucion independiente por proceso.

Etapa 4:

- Reducir redundancias.
- Mover funciones repetidas a `infrastructure/pdf`, `infrastructure/ocr` y `infrastructure/storage`.
- Optimizar solo despues de comprobar que el flujo corre igual.
