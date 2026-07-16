import re
import unicodedata

import pandas as pd

CANONICOS_REMITENTE = [
    ("TELEFONICA DEL PERU S.A.A.", ("TELEFONICADELPERU",)),
    ("AMERICA MOVIL PERU S.A.C.", ("AMERICAMOVILPERU", "CLAROAMERICAMOVIL")),
    ("ENTEL PERU S.A.", ("ENTELPERU",)),
    ("WIGO S.A.", ("WIGO",)),
    ("OPTICAL TECHNOLOGIES S.A.C.", ("OPTICALTECHNOLOGIES",)),
    ("DESARROLLOS TERRESTRES PERU S.A.", ("DESARROLLOSTERRESTRESPERU",)),
    ("DESARROLLO DE INFRAESTRUCTURA DE TELECOMUNICACIONES", ("DESARROLLODEINFRAESTRUCTURADETELECOMUNICACIONES",)),
    ("VIETTEL PERU S.A.C.", ("VIETTELPERU",)),
    ("CENTURYLINK PERU S.A.", ("CENTURYLINKPERU",)),
    ("GRUPO GTD PERU S.A.C.", ("GRUPOGTDPERU",)),
    ("GTD PERU S.A.", ("GTDPERU",)),
    ("GTO PERU S.A.", ("GTOPERU",)),
    ("TORRES UNIDAS DEL PERU S.R.L.", ("TORRESUNIDASDELPERU",)),
    ("NEXTNET S.A.C.", ("NEXTNET",)),
    ("AMERICATEL PERU S.A.", ("AMERICATELPERU",)),
    ("BTS TOWERS DE PERU S.A.C.", ("BTSTOWERSDEPERU",)),
    ("MEDIA COMMERCE PERU S.A.C.", ("MEDIACOMMERCEPERU",)),
    ("BANTEL S.A.C.", ("BANTEL", "BANDTEL")),
    ("TURRIS TELECOM DE PERU S.A.C.", ("TURRISTELECOMDEPERU",)),
    ("PHOENIX TOWER INTERNATIONAL PERU S.A.C.", ("PHOENIXTOWERINTERNATIONALPERU",)),
    ("AWC PERU S.A.C.", ("AWCPERU",)),
    ("FIBERTEL PERU S.A.", ("FIBERTELPERU",)),
    ("UFINET PERU S.A.C.", ("UFINETPERU",)),
    ("TELXIUS TORRES PERU S.A.C.", ("TELXIUSTORRESPERU",)),
    ("INVERSIONES BALESIA S.A.C.", ("INVERSIONESBALESIA", "INVERSIONESSALESIA")),
    ("ATC SITIOS DEL PERU SOCIEDAD COMERCIAL DE RESPONSABILIDAD LIMITADA", ("ATCSITIOSDELPERU",)),
    ("TELECOM BUSINESS SOLUTION S.R.L.", ("TELECOMBUSINESSSOLUTION",)),
    ("INTERNEXA PERU S.A.", ("INTERNEXAPERU",)),
    ("YACHAY TELECOMUNICACIONES S.A.C.", ("YACHAYTELECOMUNICACIONES",)),
    ("MORE STEEL TOWERS S.A.C.", ("MORESTEELTOWERS",)),
    ("SATELITAL TELECOMUNICACIONES S.A.C.", ("SATELITALTELECOMUNICACIONES",)),
    ("G & S CONTRATISTAS GENERALES S.A.C.", ("GSCONTRATISTASGENERALES", "G8SCONTRATISTASGENERALES", "G4SCONTRATISTASGENERALES")),
    ("SBA TORRES PERU S.A.", ("SBATORRESPERU",)),
    ("CELL SITE SOLUTIONS PERU S.A.C.", ("CELLSITESOLUTIONSPERU",)),
    ("JYS TOWERS E.I.R.L.", ("JYSTOWERS",)),
    ("J.R. TELECOM S.R.LTDA.", ("JRTELECOM",)),
    ("TELECOMMUNICATIONS PARTNERS S.A.C.", ("TELECOMMUNICATIONSPARTNERS",)),
    ("COAR NORTH S.A.C.", ("COARNORTH",)),
    ("NETLINE PERU S.A.", ("NETLINEPERU",)),
    ("COBRA PERU S.A.", ("COBRAPERU",)),
    ("LELITV E.I.R.L.", ("LELITV",)),
    ("WG COMUNICACIONES DIGITALES S.A.C.", ("WGCOMUNICACIONESDIGITALES",)),
    ("CATV. FULL IMAGEN S.A.C.", ("CATVFULLIMAGEN",)),
    ("CJ TELECOM S.A.C.", ("CJTELECOM",)),
    ("INGENIERIA EN GESTION DE NEGOCIOS Y OPORTUNIDADES S.A.C.", ("INGENIERIAENGESTIONDENEGOCIOSYOPORTUNIDADES",)),
    ("EMPRESA NACIONAL DE TELECOMUNICACIONES BOLIVIA S.A.C.", ("EMPRESANACIONALDETELECOMUNICACIONESBOLIVIA",)),
]

CORRECCIONES_OCR = [
    (r"OESARROLLOS", "DESARROLLOS"),
    (r"OESARROLLO", "DESARROLLO"),
    (r"'ESARROLLO", "DESARROLLO"),
    (r"TELEFONIA\s+DEL\s+PERU", "TELEFONICA DEL PERU"),
    (r"TELEFFONICA", "TELEFONICA"),
    (r"TELEFONICA\s+OEL", "TELEFONICA DEL"),
    (r"TELEFONICA\s+DEI\.", "TELEFONICA DEL"),
    (r"TELEFONICA\s+EMPRESAS\s+PERU", "TELEFONICA DEL PERU"),
    (r"TELX!US", "TELXIUS"),
    (r"OPTICAL\s+TFCHNOLOGIES", "OPTICAL TECHNOLOGIES"),
    (r"OPT!['`]?CAL", "OPTICAL"),
    (r"BANDTEL", "BANTEL"),
    (r"ENVEL\s+PERU", "ENTEL PERU"),
    (r"FISERTEL", "FIBERTEL"),
    (r"NTEL\s+PERU", "ENTEL PERU"),
    (r"E\s+NTEL", "ENTEL"),
    (r"ENTE\.\s*PER", "ENTEL PERU"),
    (r"UNTEL", "ENTEL"),
    (r"WICO", "WIGO"),
    (r"WIGOSA", "WIGO"),
    (r"SALESIA", "BALESIA"),
    (r"VIETTE['`]?(?!L)", "VIETTEL"),
    (r"PE\s+RU", "PERU"),
    (r"PFRU", "PERU"),
    (r"MOVE\.", "MOVIL"),
    (r"AMI\s+RICA", "AMERICA"),
    (r"AMF\s+RICA", "AMERICA"),
    (r"AV[!:]?\s*RICA", "AMERICA"),
    (r"AM\s*-\s*RICA", "AMERICA"),
    (r"ANE.*?MOVIL", "AMERICA MOVIL"),
    (r"AML\s+RICA", "AMERICA"),
    (r"AME\s+RICA", "AMERICA"),
    (r"AV!\s+RICA", "AMERICA"),
    (r"AMERICA\s+MOVE\.", "AMERICA MOVIL"),
    (r"IEL\s+EFONICA", "TELEFONICA"),
    (r"TELFFONICA", "TELEFONICA"),
    (r"LI\s*:+\s*:+\s*ONICA", "TELEFONICA"),
    (r"TEN\)\s*CS\s*AJEI", "TELEFONICA"),
    (r"OPT\s*'CAL", "OPTICAL"),
    (r"N\s+XINET", "NEXTNET"),
    (r"TELX\s+US", "TELXIUS"),
    (r"\bESARROLLO\b", "DESARROLLO"),
    (r"ENTEL\s+PERJ", "ENTEL PERU"),
    (r"PLRUSAA", "PERU S.A.A."),
    (r"G\s*&\s*S", "G & S"),
    (r"G\s*8\s*S", "G & S"),
    (r"G\s*4\s*S", "G & S"),
]


def quitar_tildes(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(char for char in texto if not unicodedata.combining(char))


def clave_remitente(texto):
    texto = quitar_tildes(texto).upper()
    texto = re.sub(r"[^A-Z0-9]", "", texto)
    return texto


def limpiar_remitente_ocr(texto):
    if pd.isna(texto) or str(texto).strip() == "":
        return ""

    txt = unicodedata.normalize("NFKC", str(texto))
    txt = quitar_tildes(txt).upper()
    txt = re.sub(r"[\r\n\t]+", " ", txt)
    txt = re.sub(r"[^A-Z0-9ÁÉÍÓÚÜÑ\s\.\-/&:;,()']", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" -:;,.|_'")

    for patron, reemplazo in CORRECCIONES_OCR:
        txt = re.sub(patron, reemplazo, txt, flags=re.IGNORECASE)

    # Separar sufijos legales pegados al nombre.
    txt = re.sub(r"PERU(?=S[\.\s]*A)", "PERU ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERU(?=S[\.\s]*R)", "PERU ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERU(?=SAC)", "PERU ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERU(?=SRL)", "PERU ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERUS(?=[\.\s]*A)", "PERU S", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERUS(?=[\.\s]*R)", "PERU S", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERUSAC", "PERU SAC", txt, flags=re.IGNORECASE)
    txt = re.sub(r"PERUSRL", "PERU SRL", txt, flags=re.IGNORECASE)
    txt = re.sub(r"SOCIEDAD\s+ANONIMA\s+CERRADA\s*[-]?\s*MEDIA", "SOCIEDAD ANONIMA CERRADA MEDIA", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+", " ", txt).strip(" -:;,.|_'")
    return txt


def estandarizar_sufijo_legal(texto):
    if not texto:
        return ""

    txt = texto
    txt = re.sub(r"\bS\s*A\s*A\b\.?", "S.A.A.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bS\s*A\s*C\b\.?", "S.A.C.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bS\s*A\b\.?", "S.A.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bS\s*R\s*L\s*T\s*D\s*A\b\.?", "S.R.LTDA.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bS\s*R\s*L\b\.?", "S.R.L.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bE\s*I\s*R\s*L\b\.?", "E.I.R.L.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bSAC\b\.?", "S.A.C.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bSRL\b\.?", "S.R.L.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bEIRL\b\.?", "E.I.R.L.", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+", " ", txt).strip(" -:;,.|_'")

    if txt and not txt.endswith("."):
        if re.search(r"\bS\.A\.A\.?$|\bS\.A\.C\.?$|\bS\.A\.?$|\bS\.R\.L\.?$|\bS\.R\.LTDA\.?$|\bE\.I\.R\.L\.?$", txt):
            if not txt.endswith("."):
                txt += "."

    return txt


def mapear_canonico(texto):
    clave = clave_remitente(texto)
    if not clave:
        return ""

    if "CLARO" in clave and "AMERICAMOVIL" in clave:
        return "AMERICA MOVIL PERU S.A.C."

    for canonico, patrones in CANONICOS_REMITENTE:
        if any(patron in clave for patron in patrones):
            return canonico

    return estandarizar_sufijo_legal(texto)


def normalizar_remitente(texto):
    limpio = limpiar_remitente_ocr(texto)
    if not limpio:
        return ""

    canonico = mapear_canonico(limpio)
    if canonico != estandarizar_sufijo_legal(limpio):
        return canonico

    return estandarizar_sufijo_legal(limpio)


def normalizar_columna_remitente(df, columna="remitente", guardar_original=True):
    df = df.copy()
    if guardar_original and "remitente_original" not in df.columns:
        df["remitente_original"] = df[columna]

    df[columna] = df[columna].apply(normalizar_remitente)
    return df
