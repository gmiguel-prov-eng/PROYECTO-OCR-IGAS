"""
Utilidades compartidas de inventarios (clave hoja_ruta, asunto, OCR, carga tablas).

Fuente única: no redefinir normalizar_hoja_ruta / limpiar_asunto en use_cases.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

PATRON_ASUNTO_PROYECTO = re.compile(r"^.*?(?=\bproyecto\b)", flags=re.IGNORECASE)
# Alias histórico (reporte subsanados / notebooks).
PATRON_ASUNTO = PATRON_ASUNTO_PROYECTO
# Prefijo oficial: "Oficio 0576 2019 MTC 26 …EMPRESA.pdf" → cortar antes de la razón social.
PATRON_NOMBRE_OFICIO = re.compile(
    r"^(Oficio[\s\-]+\d+[\s\-\.]+\d{4}[\s\-]+(?:MTC[\s\-]*)?\d*)",
    flags=re.IGNORECASE,
)
PATRON_NOMBRE_OFICIO_FALLBACK = re.compile(
    r"^(Oficio.+?\bMTC[\s\-]*\d+)",
    flags=re.IGNORECASE,
)

COLUMNAS_ENTREGA = ["hoja_ruta", "nombre_informe", "remitente", "asunto"]
# Flujo 2: oficio = código corto del archivo (sin empresa ni .pdf).
COLUMNAS_ENTREGA_OFICIOS = ["hoja_ruta", "oficio", "remitente", "asunto"]
# Inventario unificado (lote entrega / Drive).
COLUMNAS_INVENTARIO_LOTE = [
    "hoja_ruta",
    "oficio",
    "informe",
    "tipo_IGA",
    "NOMBRE DEL PROYECTO",
    "proyecto_url",
    "administrador",
    "estado",
]
PATRON_PREFIJO_PROYECTO = re.compile(
    r"^proyecto\s*(?:[:.\-]|\s+(?:de|del|de\s+la|para)\b)?\s*",
    flags=re.IGNORECASE,
)


def normalizar_hoja_ruta(texto) -> str:
    texto = str(texto or "").strip()
    if not texto or texto.lower() in {"nan", "none"}:
        return ""
    return re.sub(r"[^A-Z0-9]", "", texto.upper())


def texto_vacio(valor) -> bool:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return True
    t = str(valor).strip()
    return not t or t.lower() in {"nan", "none"}


def limpiar_asunto(texto) -> str:
    """Deja el asunto desde la palabra 'proyecto'."""
    if texto_vacio(texto):
        return ""
    return PATRON_ASUNTO_PROYECTO.sub("", str(texto)).strip()


def limpiar_nombre_proyecto(texto) -> str:
    """
    Nombre de proyecto para inventario unificado:
    desde 'proyecto', quita el prefijo PROYECTO:/DE, mayúsculas, espacios colapsados.
    """
    if texto_vacio(texto):
        return ""
    t = limpiar_asunto(texto)
    if not t:
        t = str(texto).strip()
    t = PATRON_PREFIJO_PROYECTO.sub("", t)
    t = re.sub(r"[\x00-\x1f]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" .-_;,:")
    return t.upper()


def obtener_hojas_ruta(df):
    if df is None or "hoja_ruta" not in getattr(df, "columns", []):
        return set()
    return {
        normalizar_hoja_ruta(value)
        for value in df["hoja_ruta"].astype(str)
        if str(value).strip()
    }


def cargar_tabla(ruta, preferir_hoja_limpia=True):
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe: {ruta}")

    suf = ruta.suffix.lower()
    if suf in {".xlsx", ".xls"}:
        xl = pd.ExcelFile(ruta)
        if preferir_hoja_limpia and "inventario_final_limpio" in xl.sheet_names:
            hoja = "inventario_final_limpio"
        else:
            hoja = 0 if "SELECCIONADOS" not in xl.sheet_names else "SELECCIONADOS"
            if hoja == 0 and xl.sheet_names:
                hoja = xl.sheet_names[0]
        return pd.read_excel(ruta, sheet_name=hoja, dtype=str)

    return pd.read_csv(ruta, encoding="utf-8-sig", sep=None, engine="python", dtype=str)


def cargar_ocr_ficha(reporte_json, incluir_indice_archivo=False):
    """
    Carga reporte_total.json indexado por hoja_ruta.
    Si incluir_indice_archivo=True, retorna (ocr_unico, por_archivo_pdf).
    """
    reporte_json = Path(reporte_json)
    if not reporte_json.exists():
        raise FileNotFoundError(f"No existe OCR JSON: {reporte_json}")

    ocr_raw = pd.read_json(reporte_json, orient="records")
    if "hoja_ruta" not in ocr_raw.columns:
        raise KeyError("reporte_total.json sin columna hoja_ruta")

    ocr = ocr_raw.copy()
    ocr["_key"] = ocr["hoja_ruta"].map(normalizar_hoja_ruta)
    ocr = ocr[ocr["_key"].astype(bool)].drop_duplicates(subset=["_key"], keep="first")

    if not incluir_indice_archivo:
        return ocr

    por_archivo = {}
    if "archivo_pdf" in ocr_raw.columns:
        for _, r in ocr_raw.iterrows():
            stem = Path(str(r.get("archivo_pdf") or "")).stem
            key = normalizar_hoja_ruta(stem)
            if key and key not in por_archivo:
                por_archivo[key] = r
    return ocr, por_archivo


def enriquecer_con_ocr(df, ocr, columnas_ocr=None):
    """Left-merge por hoja_ruta normalizada. Aporta remitente/asunto/n_doc si faltan."""
    columnas_ocr = columnas_ocr or ["remitente", "asunto", "n_doc"]
    base = df.copy()
    base["_key"] = base["hoja_ruta"].map(normalizar_hoja_ruta)

    disponibles = [c for c in columnas_ocr if c in ocr.columns]
    if not disponibles:
        return base.drop(columns=["_key"], errors="ignore")

    extra = ocr[["_key"] + disponibles].copy()
    for col in disponibles:
        extra = extra.rename(columns={col: f"{col}_ocr"})

    merged = base.merge(extra, on="_key", how="left")

    if "remitente_ocr" in merged.columns:
        if "remitente" not in merged.columns:
            merged["remitente"] = merged["remitente_ocr"]
        else:
            vacio = merged["remitente"].map(texto_vacio)
            merged.loc[vacio, "remitente"] = merged.loc[vacio, "remitente_ocr"]
        merged = merged.drop(columns=["remitente_ocr"])

    if "asunto_ocr" in merged.columns:
        if "asunto" not in merged.columns:
            merged["asunto"] = merged["asunto_ocr"]
        else:
            vacio = merged["asunto"].map(texto_vacio)
            merged.loc[vacio, "asunto"] = merged.loc[vacio, "asunto_ocr"]
        merged = merged.drop(columns=["asunto_ocr"])

    if "n_doc_ocr" in merged.columns:
        if "nombre_informe" not in merged.columns:
            merged["nombre_informe"] = merged["n_doc_ocr"]
        else:
            vacio = merged["nombre_informe"].map(texto_vacio)
            merged.loc[vacio, "nombre_informe"] = merged.loc[vacio, "n_doc_ocr"]
        merged = merged.drop(columns=["n_doc_ocr"])

    return merged.drop(columns=["_key"], errors="ignore")


def filtrar_remitente_no_vacio(df):
    if "remitente" not in df.columns:
        return df.copy()
    return df.loc[~df["remitente"].map(texto_vacio)].copy()


def deduplicar_hoja_ruta(df, keep="first"):
    if "hoja_ruta" not in df.columns:
        return df.copy()
    out = df.copy()
    out["_key"] = out["hoja_ruta"].map(normalizar_hoja_ruta)
    out = out[out["_key"].astype(bool)].drop_duplicates(subset=["_key"], keep=keep)
    return out.drop(columns=["_key"])


def aplicar_limpieza_asunto(df, columna="asunto"):
    out = df.copy()
    if columna not in out.columns:
        out[columna] = ""
    out[columna] = out[columna].map(limpiar_asunto)
    return out


def seleccionar_columnas_entrega(df, extras=None):
    cols = list(COLUMNAS_ENTREGA)
    if extras:
        cols.extend(c for c in extras if c in df.columns and c not in cols)
    out = df.copy()
    for c in COLUMNAS_ENTREGA:
        if c not in out.columns:
            out[c] = ""
    return out[cols].copy()


def limpiar_celda_nan(texto) -> str:
    if texto_vacio(texto):
        return ""
    return str(texto).strip()


def limpiar_nombre_oficio(nombre) -> str:
    """
    Deja el código del oficio y descarta razón social / extensión.
    Ej.: 'Oficio 0576 2019 MTC 26 MEDIA COMMERCE PERÚ SAC.pdf'
      → 'Oficio 0576 2019 MTC 26'
    """
    t = limpiar_celda_nan(nombre)
    if not t:
        return ""
    t = Path(t).name
    if t.lower().endswith(".pdf"):
        t = t[:-4]

    m = PATRON_NOMBRE_OFICIO.match(t)
    if m:
        return re.sub(r"[\s\-\.]+$", "", m.group(1)).strip()

    m2 = PATRON_NOMBRE_OFICIO_FALLBACK.match(t)
    if m2:
        return m2.group(1).strip()

    return t.strip()


def elegir_remitente(remitente, empresa=""):
    """Prefiere el texto más completo entre remitente y empresa (flujo oficios)."""
    r = limpiar_celda_nan(remitente)
    e = limpiar_celda_nan(empresa)
    if not r:
        return e
    if not e:
        return r
    # Misma empresa con/sin puntos: usar la más larga (suele estar más completa).
    if _normalizar_clave_texto(r) == _normalizar_clave_texto(e) or _normalizar_clave_texto(r) in _normalizar_clave_texto(e) or _normalizar_clave_texto(e) in _normalizar_clave_texto(r):
        return e if len(e) >= len(r) else r
    # Distintos: empresa de carpeta OFICIOS suele ser la clasificada.
    return e if len(e) > len(r) else r


def _normalizar_clave_texto(texto) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(texto or "").upper())


def preparar_campos_entrega_oficios(df):
    """
    Flujo 2 — columnas finales:
      hoja_ruta | oficio (nombre archivo) | remitente (vs empresa) | asunto
    """
    out = df.copy()
    if "archivo_oficio" in out.columns:
        fuente_oficio = out["archivo_oficio"]
    elif "archivo" in out.columns:
        fuente_oficio = out["archivo"]
    else:
        fuente_oficio = pd.Series([""] * len(out), index=out.index)
    out["oficio"] = fuente_oficio.map(limpiar_nombre_oficio)

    if "remitente" not in out.columns:
        out["remitente"] = ""
    empresa = out["empresa"] if "empresa" in out.columns else pd.Series([""] * len(out), index=out.index)
    out["remitente"] = [
        elegir_remitente(r, e) for r, e in zip(out["remitente"].tolist(), empresa.tolist())
    ]
    return out


def limpiar_inventario_dataframe(df, ocr, extras_entrega=None):
    """
    Pipeline REVISION_FINAL (flujo 1 / expediente_final):
    enriquecer OCR → quitar remitente vacío → dedupe hoja_ruta → limpiar asunto → columnas entrega.
    """
    alimentado = enriquecer_con_ocr(df, ocr)
    sin_vacios = filtrar_remitente_no_vacio(alimentado)
    sin_dup = deduplicar_hoja_ruta(sin_vacios)
    con_asunto = aplicar_limpieza_asunto(sin_dup)
    return seleccionar_columnas_entrega(con_asunto, extras=extras_entrega)


def limpiar_inventario_oficios(df, ocr):
    """
    Pipeline entrega flujo 2 (reporte_solicitud_oficio):
    enriquecer asunto/OCR → oficio=archivo → remitente vs empresa → filtros → columnas de entrega.
    """
    alimentado = enriquecer_con_ocr(df, ocr)
    preparado = preparar_campos_entrega_oficios(alimentado)
    sin_vacios = filtrar_remitente_no_vacio(preparado)
    sin_dup = deduplicar_hoja_ruta(sin_vacios)
    con_asunto = aplicar_limpieza_asunto(sin_dup)
    out = con_asunto.copy()
    for c in COLUMNAS_ENTREGA_OFICIOS:
        if c not in out.columns:
            out[c] = ""
    return out[COLUMNAS_ENTREGA_OFICIOS].copy()


def _coalesce_serie(serie) -> str:
    for valor in serie:
        if not texto_vacio(valor):
            return limpiar_celda_nan(valor)
    return ""


def _estandarizar_fuente_inventario(df, fuente: str) -> pd.DataFrame:
    """Normaliza F1 / oficios / subsanados a columnas intermedias de fusión."""
    vacio = pd.DataFrame(
        columns=["hoja_ruta", "oficio", "informe", "NOMBRE DEL PROYECTO", "administrador", "_fuente"]
    )
    if df is None or len(df) == 0:
        return vacio

    out = df.copy()
    out.columns = [str(c).lstrip("\ufeff").strip() for c in out.columns]
    if "hoja_ruta" not in out.columns:
        raise KeyError(f"Sin columna hoja_ruta en fuente {fuente}")

    n = len(out)
    oficio = out["oficio"] if "oficio" in out.columns else pd.Series([""] * n, index=out.index)
    if "nombre_informe" in out.columns:
        informe = out["nombre_informe"]
    elif "informe" in out.columns:
        informe = out["informe"]
    else:
        informe = pd.Series([""] * n, index=out.index)

    asunto = out["asunto"] if "asunto" in out.columns else pd.Series([""] * n, index=out.index)
    remitente = out["remitente"] if "remitente" in out.columns else pd.Series([""] * n, index=out.index)
    if "administrador" in out.columns:
        remitente = [
            elegir_remitente(r, a)
            for r, a in zip(remitente.tolist(), out["administrador"].tolist())
        ]

    base = pd.DataFrame(
        {
            "hoja_ruta": out["hoja_ruta"].map(limpiar_celda_nan),
            "oficio": pd.Series(oficio, index=out.index).map(limpiar_celda_nan),
            "informe": pd.Series(informe, index=out.index).map(limpiar_celda_nan),
            "NOMBRE DEL PROYECTO": pd.Series(asunto, index=out.index).map(limpiar_nombre_proyecto),
            "administrador": pd.Series(remitente, index=out.index).map(limpiar_celda_nan),
            "_fuente": fuente,
        }
    )
    return base[base["hoja_ruta"].astype(bool)].copy()


def consolidar_inventario_lote(dfs_por_fuente: dict) -> pd.DataFrame:
    """
    Une inventarios limpios (F1, oficios, subsanados) en esquema de lote.
    proyecto_url queda vacío (se completa tras subir a Drive).
    """
    partes = []
    for fuente, df in dfs_por_fuente.items():
        if df is None or len(df) == 0:
            continue
        partes.append(_estandarizar_fuente_inventario(df, fuente))

    if not partes:
        return pd.DataFrame(columns=COLUMNAS_INVENTARIO_LOTE)

    todo = pd.concat(partes, ignore_index=True)
    todo["_key"] = todo["hoja_ruta"].map(normalizar_hoja_ruta)
    todo = todo[todo["_key"].astype(bool)]

    filas = []
    for key, grupo in todo.groupby("_key", sort=False):
        hoja = _coalesce_serie(grupo["hoja_ruta"])
        filas.append(
            {
                "hoja_ruta": hoja,
                "oficio": _coalesce_serie(grupo["oficio"]),
                "informe": _coalesce_serie(grupo["informe"]),
                "tipo_IGA": "Ficha",
                "NOMBRE DEL PROYECTO": _coalesce_serie(grupo["NOMBRE DEL PROYECTO"]),
                "proyecto_url": "",
                "administrador": _coalesce_serie(grupo["administrador"]),
                "estado": "conforme",
            }
        )

    return pd.DataFrame(filas, columns=COLUMNAS_INVENTARIO_LOTE)
