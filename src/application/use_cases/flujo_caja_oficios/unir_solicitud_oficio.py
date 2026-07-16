import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import fitz
import pandas as pd

from infrastructure.storage.filesystem import asegurar_directorio

COLUMNAS_REPORTE = [
    "hoja_ruta",
    "caja",
    "carpeta",
    "remitente",
    "match_oficio",
    "empresa",
    "archivo_solicitud",
    "archivo_oficio",
    "pdf_salida",
    "paginas_solicitud",
    "paginas_oficio",
    "paginas_total",
]


def ejecutar(
    config,
    logger,
    forzar=False,
    empresas=None,
    pdfs_dir=None,
    reporte=None,
    omitir_hojas_de=None,
):
    """
    Crea PDF nuevos (solicitud + oficio). No mueve ni modifica los PDF origen.
    Los PDF unidos se guardan en paths.output...expedientes_oficio_pdfs (extension).
    El reporte CSV se acumula en expedientes_oficio (local).

    Opcional (aislar reprocesos, p. ej. Telefónica/Entel):
      empresas: filtro por nombre de carpeta empresa
      pdfs_dir / reporte: carpeta y CSV propios (no mezclar con extension/)
      omitir_hojas_de: CSV de referencia cuyas hoja_ruta ya entregadas se saltan
    """
    rutas = config["paths"]
    reportes = Path(rutas["work"]["fichas_oficios"]["reportes"])
    seleccionados = Path(rutas["work"]["analisis"]["pdfs_clasificados"]["seleccionados"])
    oficios_root = Path(rutas["input"]["oficios"])

    salida_cfg = rutas["output"]["resultados_finales"].get("expedientes_oficio")
    if salida_cfg:
        salida_root = Path(salida_cfg)
    else:
        salida_root = Path(rutas["output"]["resultados_finales"]["expedientes"]).parent / "expedientes_oficio"

    pdfs_cfg = rutas["output"]["resultados_finales"].get("expedientes_oficio_pdfs")
    if pdfs_dir:
        pdfs_dir = Path(pdfs_dir)
    elif pdfs_cfg:
        pdfs_dir = Path(pdfs_cfg)
    else:
        pdfs_dir = salida_root / "pdfs"

    asegurar_directorio(pdfs_dir)
    asegurar_directorio(salida_root)

    ruta_match = _resolver_entrada(reportes, "solicitud_oficio", preferidas=(".csv", ".xlsx", ".xls"))
    csv_reporte = Path(reporte) if reporte else (salida_root / "reporte_solicitud_oficio.csv")
    # Si la salida es aislada y no piden otro omitir: no repetir lo ya en el reporte principal.
    if omitir_hojas_de is None and reporte and Path(reporte).resolve() != (salida_root / "reporte_solicitud_oficio.csv").resolve():
        omitir_hojas_de = salida_root / "reporte_solicitud_oficio.csv"

    logger.info("Entrada match: %s", ruta_match)
    logger.info("PDFs solicitud (origen, no se mueven): %s", seleccionados)
    logger.info("PDFs oficio (origen, no se mueven): %s", oficios_root)
    logger.info("Salida PDF unidos: %s", pdfs_dir)
    logger.info("Reporte local: %s", csv_reporte)

    df = cargar_tabla(ruta_match)
    if "match_oficio" not in df.columns:
        raise KeyError("No existe columna 'match_oficio' en solicitud_oficio")

    candidatos = df[df["match_oficio"].map(_es_match_true)].copy()
    if empresas:
        empresas_norm = [_normalizar_texto(e) for e in empresas if str(e).strip()]
        if "empresa" not in candidatos.columns:
            raise KeyError("No existe columna 'empresa' para filtrar")
        mask = candidatos["empresa"].map(
            lambda v: any(f in _normalizar_texto(v) for f in empresas_norm)
        )
        antes = len(candidatos)
        candidatos = candidatos[mask].copy()
        logger.info("Filtro empresas %s: %s → %s candidatos", empresas, antes, len(candidatos))

    reporte_previo = _cargar_reporte(csv_reporte)
    hojas_previas = set()
    if not reporte_previo.empty and "hoja_ruta" in reporte_previo.columns:
        hojas_previas = {
            str(h).strip()
            for h in reporte_previo["hoja_ruta"].tolist()
            if str(h).strip()
        }

    hojas_omitir = set(hojas_previas)
    if omitir_hojas_de:
        ref = Path(omitir_hojas_de)
        if ref.exists():
            ref_df = _cargar_reporte(ref)
            if not ref_df.empty and "hoja_ruta" in ref_df.columns:
                extra = {
                    str(h).strip()
                    for h in ref_df["hoja_ruta"].tolist()
                    if str(h).strip()
                }
                hojas_omitir |= extra
                logger.info(
                    "Omitir hoja_ruta ya entregadas en %s: %s (+%s únicas)",
                    ref,
                    len(extra),
                    len(hojas_omitir) - len(hojas_previas),
                )
        else:
            logger.warning("No existe reporte de referencia para omitir: %s", ref)

    logger.info(
        "Filas con match_oficio=True: %s | ya omitidas/en reporte: %s | forzar=%s",
        len(candidatos),
        len(hojas_omitir),
        forzar,
    )

    registros_nuevos = []
    omitidos = 0
    errores = 0

    for _, row in candidatos.iterrows():
        hoja = str(row.get("hoja_ruta") or "").strip()
        nombre_salida = f"{_nombre_seguro(hoja) or 'sin_hoja_ruta'}.pdf"
        destino = pdfs_dir / nombre_salida

        if not forzar and hoja:
            # Reporte propio: omitir si ya existe el PDF en esta carpeta.
            if hoja in hojas_previas and destino.exists():
                omitidos += 1
                continue
            # Salida aislada: no duplicar lo ya entregado en el reporte principal.
            if hoja in hojas_omitir and hoja not in hojas_previas:
                omitidos += 1
                continue

        try:
            pdf_solicitud = resolver_pdf_solicitud(seleccionados, row)
            pdf_oficio = resolver_pdf_oficio(oficios_root, row)
        except FileNotFoundError as exc:
            errores += 1
            logger.warning("%s: %s", hoja or "sin_hoja", exc)
            continue

        if not hoja:
            nombre_salida = f"{_nombre_seguro(Path(pdf_solicitud).stem)}.pdf"
            destino = pdfs_dir / nombre_salida

        try:
            # Crea un PDF nuevo; no mueve solicitud ni oficio.
            pag_sol, pag_ofi, pag_total = crear_pdf_unido(pdf_solicitud, pdf_oficio, destino)
        except Exception:
            errores += 1
            logger.exception("No se pudo crear PDF unido para %s", hoja)
            continue

        registros_nuevos.append(
            {
                "hoja_ruta": hoja,
                "caja": row.get("caja", ""),
                "carpeta": row.get("carpeta", ""),
                "remitente": row.get("remitente", ""),
                "match_oficio": True,
                "empresa": row.get("empresa", ""),
                "archivo_solicitud": Path(pdf_solicitud).name,
                "archivo_oficio": Path(pdf_oficio).name,
                "pdf_salida": str(destino),
                "paginas_solicitud": pag_sol,
                "paginas_oficio": pag_ofi,
                "paginas_total": pag_total,
            }
        )
        logger.info(
            "PDF nuevo creado: %s <- %s + %s",
            nombre_salida,
            Path(pdf_solicitud).name,
            Path(pdf_oficio).name,
        )

    reporte = actualizar_reporte_acumulado(reporte_previo, registros_nuevos, logger)
    _guardar_reporte_csv(reporte, csv_reporte, logger)

    resumen = {
        "proceso": "unir_solicitud_oficio",
        "estado": "completado",
        "modo": "crea_pdf_nuevo_sin_mover_origenes",
        "empresas_filtro": empresas or "todas",
        "candidatos_match": len(candidatos),
        "pdfs_creados_esta_corrida": len(registros_nuevos),
        "omitidos_ya_existentes": omitidos,
        "errores": errores,
        "filas_reporte_total": len(reporte),
        "salida_pdfs": str(pdfs_dir),
        "reporte": str(csv_reporte),
    }
    logger.info("Union completada: %s", resumen)
    return resumen


def crear_pdf_unido(pdf_solicitud, pdf_oficio, destino):
    """Genera un PDF nuevo concatenando solicitud + oficio. No altera los originales."""
    destino = Path(destino)
    asegurar_directorio(destino.parent)

    with fitz.open(pdf_solicitud) as doc_sol, fitz.open(pdf_oficio) as doc_ofi:
        pag_sol = doc_sol.page_count
        pag_ofi = doc_ofi.page_count
        unido = fitz.open()
        unido.insert_pdf(doc_sol)
        unido.insert_pdf(doc_ofi)
        unido.save(destino)
        pag_total = unido.page_count
        unido.close()

    return pag_sol, pag_ofi, pag_total


def _guardar_reporte_csv(reporte, csv_reporte, logger=None, intentos=5):
    """Guarda el reporte con reintentos (p. ej. si Excel lo tiene abierto)."""
    import time as _time

    csv_reporte = Path(csv_reporte)
    asegurar_directorio(csv_reporte.parent)
    tmp = csv_reporte.with_suffix(csv_reporte.suffix + ".tmp")
    last_err = None
    for i in range(intentos):
        try:
            reporte.to_csv(tmp, index=False, encoding="utf-8-sig")
            os.replace(tmp, csv_reporte)
            return
        except PermissionError as exc:
            last_err = exc
            if logger:
                logger.warning(
                    "No se pudo guardar reporte (intento %s/%s): %s",
                    i + 1,
                    intentos,
                    csv_reporte,
                )
            _time.sleep(2)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    raise last_err


def actualizar_reporte_acumulado(reporte_previo, registros_nuevos, logger=None):
    nuevos = pd.DataFrame(registros_nuevos, columns=COLUMNAS_REPORTE)
    if reporte_previo is None or reporte_previo.empty:
        return nuevos if not nuevos.empty else pd.DataFrame(columns=COLUMNAS_REPORTE)

    previo = reporte_previo.copy()
    for col in COLUMNAS_REPORTE:
        if col not in previo.columns:
            previo[col] = ""

    if nuevos.empty:
        return previo[COLUMNAS_REPORTE]

    hojas_nuevas = set(str(h).strip() for h in nuevos["hoja_ruta"].tolist() if str(h).strip())
    resto = previo[~previo["hoja_ruta"].astype(str).str.strip().isin(hojas_nuevas)].copy()
    resultado = pd.concat([resto, nuevos], ignore_index=True)[COLUMNAS_REPORTE]
    if logger:
        logger.info(
            "Reporte acumulado: previas=%s | nuevas/actualizadas=%s | total=%s",
            len(resto),
            len(nuevos),
            len(resultado),
        )
    return resultado


def resolver_pdf_solicitud(seleccionados_root, row):
    seleccionados_root = Path(seleccionados_root)
    archivo = str(row.get("archivo_pdf") or "").strip()
    if not archivo:
        raise FileNotFoundError("Fila sin archivo_pdf de solicitud")

    caja = str(row.get("caja") or "").strip()
    carpeta = str(row.get("carpeta") or "").strip()
    pdf_origen = str(row.get("pdf_origen") or "").strip()

    candidatos = [p for p in seleccionados_root.rglob(archivo) if p.is_file()]
    if not candidatos:
        raise FileNotFoundError(f"No se encontro PDF solicitud: {archivo}")

    if caja or carpeta or pdf_origen:
        filtrados = []
        for path in candidatos:
            partes = {p.upper() for p in path.parts}
            ok = True
            if caja and caja.upper() not in partes:
                ok = False
            if carpeta and carpeta.upper() not in partes:
                ok = False
            if pdf_origen and pdf_origen.upper() not in {p.upper() for p in path.parts}:
                if pdf_origen.upper() not in path.as_posix().upper():
                    ok = False
            if ok:
                filtrados.append(path)
        if filtrados:
            candidatos = filtrados

    return candidatos[0]


def resolver_pdf_oficio(oficios_root, row):
    oficios_root = Path(oficios_root)
    empresa = str(row.get("empresa") or "").strip()
    archivo = str(row.get("archivo") or "").strip()
    if not empresa or not archivo:
        raise FileNotFoundError("Fila sin empresa/archivo de oficio")

    base = oficios_root / empresa
    if not base.exists():
        raise FileNotFoundError(f"No existe carpeta de empresa: {base}")

    for estado in ("COMPLETO", "PARCIAL", "INCOMPLETO"):
        candidato = base / estado / archivo
        if candidato.exists():
            return candidato

    candidato = base / archivo
    if candidato.exists():
        return candidato

    hits = [p for p in base.rglob(archivo) if p.is_file()]
    if hits:
        return _preferir_estado(hits)

    # Nombres en disco suelen traer espacios dobles o acentos distintos al CSV.
    pdfs = _listar_pdfs_empresa(str(base))
    clave = _clave_nombre_archivo(archivo)
    por_clave = [p for p in pdfs if _clave_nombre_archivo(p.name) == clave]
    if por_clave:
        return _preferir_estado(por_clave)

    num = _extraer_numero_oficio(archivo)
    if num is not None:
        por_num = [p for p in pdfs if _extraer_numero_oficio(p.name) == num]
        if len(por_num) == 1:
            return por_num[0]
        if len(por_num) > 1:
            return _preferir_estado(por_num)

    raise FileNotFoundError(f"No se encontro PDF oficio: {empresa}/{archivo}")


def _normalizar_espacios(texto):
    return re.sub(r"\s+", " ", str(texto or "").strip())


def _normalizar_texto(texto):
    t = unicodedata.normalize("NFD", str(texto or "").strip().upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]", "", t)


def _clave_nombre_archivo(texto):
    """Clave tolerante a espacios múltiples y acentos."""
    t = _normalizar_espacios(texto).lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return "".join(c for c in t if ord(c) < 128)


def _extraer_numero_oficio(nombre):
    """
    Número tras 'Oficio'. Si el primer token es un año (20xx) seguido de MTC,
    se interpreta como oficio sin número (clave vacía).
    """
    n = _normalizar_espacios(nombre)
    m = re.match(r"(?i)^Oficio\s+(\d+)\s+", n)
    if not m:
        return None
    num = m.group(1)
    resto = n[m.end() :].lstrip()
    if len(num) == 4 and num.startswith("20") and resto.upper().startswith("MTC"):
        return ""
    return num


def _preferir_estado(paths):
    orden = {"COMPLETO": 0, "PARCIAL": 1, "INCOMPLETO": 2}

    def key(p):
        partes = {x.upper() for x in Path(p).parts}
        for estado, peso in orden.items():
            if estado in partes:
                return (peso, str(p).lower())
        return (3, str(p).lower())

    return sorted(paths, key=key)[0]


@lru_cache(maxsize=64)
def _listar_pdfs_empresa(base_str):
    base = Path(base_str)
    return tuple(p for p in base.rglob("*.pdf") if p.is_file())


def _es_match_true(valor):
    if isinstance(valor, bool):
        return valor
    texto = str(valor or "").strip().lower()
    return texto in {"true", "1", "si", "sí", "yes"}


def _cargar_reporte(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        return pd.DataFrame(columns=COLUMNAS_REPORTE)
    return cargar_tabla(ruta)


def cargar_tabla(ruta):
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    suffix = ruta.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(ruta)

    muestra = ruta.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cabecera = muestra[0] if muestra else ""
    sep = ";" if cabecera.count(";") > cabecera.count(",") else ","
    return pd.read_csv(ruta, sep=sep, encoding="utf-8-sig")


def _resolver_entrada(carpeta, stem, preferidas=(".csv", ".xlsx", ".xls")):
    carpeta = Path(carpeta)
    for ext in preferidas:
        candidato = carpeta / f"{stem}{ext}"
        if candidato.exists():
            return candidato
    existentes = sorted(carpeta.glob(f"{stem}.*"))
    if existentes:
        return existentes[0]
    raise FileNotFoundError(f"No se encontro {stem}.* en {carpeta}")


def _nombre_seguro(texto):
    texto = str(texto or "").strip()
    if not texto:
        return ""
    return re.sub(r'[<>:"/\\|?*]', "_", texto)
