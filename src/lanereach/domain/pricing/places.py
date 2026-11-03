"""Recognising ports, airports and cities in rate sheets and quote requests.

A place written as a UN/LOCODE (`AEJEA`) or IATA code (`DXB`) is used as is; a code in brackets
(`Jebel Ali (AEJEA)`) is taken out; well-known names map to their code through a small alias
table. Anything else is compared by its normalised name, so `Port of Chennai` and `chennai`
still match each other.
"""

from __future__ import annotations

import re
import unicodedata

_LOCODE = re.compile(r"^[A-Z]{2}[A-Z2-9]{3}$")
_IATA = re.compile(r"^[A-Z]{3}$")
_BRACKETED = re.compile(r"\(([A-Z]{2}[A-Z2-9]{3}|[A-Z]{3})\)")
_NOISE = re.compile(
    r"\b(port|of|the|seaport|airport|intl|international|terminal|harbour|harbor|city|icd|cfs"
    r"|free zone|fz)\b"
)

# Major freight places (lower-case names -> UN/LOCODE). Extend as lanes grow.
ALIASES: dict[str, str] = {
    "jebel ali": "AEJEA",
    "dubai": "AEDXB",
    "abu dhabi": "AEAUH",
    "khalifa": "AEKHL",
    "sharjah": "AESHJ",
    "dammam": "SADMM",
    "jeddah": "SAJED",
    "riyadh": "SARUH",
    "doha": "QADOH",
    "hamad": "QAHMD",
    "shuwaikh": "KWSWK",
    "sohar": "OMSOH",
    "salalah": "OMSLL",
    "chennai": "INMAA",
    "madras": "INMAA",
    "nhava sheva": "INNSA",
    "jnpt": "INNSA",
    "mumbai": "INBOM",
    "mundra": "INMUN",
    "kolkata": "INCCU",
    "cochin": "INCOK",
    "colombo": "LKCMB",
    "karachi": "PKKHI",
    "chittagong": "BDCGP",
    "shanghai": "CNSHA",
    "ningbo": "CNNGB",
    "shenzhen": "CNSZX",
    "yantian": "CNYTN",
    "qingdao": "CNTAO",
    "tianjin": "CNTSN",
    "xiamen": "CNXMN",
    "guangzhou": "CNCAN",
    "hong kong": "HKHKG",
    "singapore": "SGSIN",
    "port klang": "MYPKG",
    "klang": "MYPKG",
    "busan": "KRPUS",
    "tokyo": "JPTYO",
    "yokohama": "JPYOK",
    "ho chi minh": "VNSGN",
    "haiphong": "VNHPH",
    "laem chabang": "THLCH",
    "bangkok": "THBKK",
    "jakarta": "IDJKT",
    "rotterdam": "NLRTM",
    "amsterdam": "NLAMS",
    "antwerp": "BEANR",
    "hamburg": "DEHAM",
    "bremerhaven": "DEBRV",
    "felixstowe": "GBFXT",
    "southampton": "GBSOU",
    "london": "GBLON",
    "le havre": "FRLEH",
    "marseille": "FRMRS",
    "paris": "FRPAR",
    "valencia": "ESVLC",
    "barcelona": "ESBCN",
    "algeciras": "ESALG",
    "madrid": "ESMAD",
    "genoa": "ITGOA",
    "la spezia": "ITSPE",
    "piraeus": "GRPIR",
    "istanbul": "TRIST",
    "ambarli": "TRAMR",
    "mersin": "TRMER",
    "izmir": "TRIZM",
    "gdansk": "PLGDN",
    "new york": "USNYC",
    "newark": "USEWR",
    "savannah": "USSAV",
    "houston": "USHOU",
    "los angeles": "USLAX",
    "long beach": "USLGB",
    "chicago": "USCHI",
    "miami": "USMIA",
    "toronto": "CATOR",
    "montreal": "CAMTR",
    "vancouver": "CAVAN",
    "halifax": "CAHAL",
    "santos": "BRSSZ",
    "veracruz": "MXVER",
    "manzanillo": "MXZLO",
    "durban": "ZADUR",
    "mombasa": "KEMBA",
    "lagos": "NGLOS",
    "apapa": "NGAPP",
    "tema": "GHTEM",
    "alexandria": "EGALY",
    "port said": "EGPSD",
    "sokhna": "EGSOK",
    "aqaba": "JOAQJ",
    "sydney": "AUSYD",
    "melbourne": "AUMEL",
}


# Airport codes -> the city's UN/LOCODE, so an air rate "DXB" matches a request for "Dubai".
AIRPORTS: dict[str, str] = {
    "DXB": "AEDXB",
    "DWC": "AEDXB",
    "AUH": "AEAUH",
    "SHJ": "AESHJ",
    "DOH": "QADOH",
    "RUH": "SARUH",
    "JED": "SAJED",
    "DMM": "SADMM",
    "KWI": "KWKWI",
    "MCT": "OMMCT",
    "BAH": "BHBAH",
    "MAA": "INMAA",
    "BOM": "INBOM",
    "DEL": "INDEL",
    "BLR": "INBLR",
    "CCU": "INCCU",
    "CMB": "LKCMB",
    "KHI": "PKKHI",
    "IST": "TRIST",
    "FRA": "DEFRA",
    "MUC": "DEMUC",
    "AMS": "NLAMS",
    "LHR": "GBLON",
    "LGW": "GBLON",
    "CDG": "FRPAR",
    "MAD": "ESMAD",
    "BCN": "ESBCN",
    "MXP": "ITMIL",
    "JFK": "USNYC",
    "EWR": "USEWR",
    "ORD": "USCHI",
    "LAX": "USLAX",
    "MIA": "USMIA",
    "ATL": "USATL",
    "YYZ": "CATOR",
    "YUL": "CAMTR",
    "YVR": "CAVAN",
    "HKG": "HKHKG",
    "SIN": "SGSIN",
    "PVG": "CNSHA",
    "CAN": "CNCAN",
    "SZX": "CNSZX",
    "BKK": "THBKK",
    "KUL": "MYKUL",
    "NRT": "JPTYO",
    "ICN": "KRSEL",
    "SYD": "AUSYD",
    "JNB": "ZAJNB",
    "NBO": "KENBO",
    "CAI": "EGCAI",
}


def normalise_place(text: str) -> str:
    """Lower-case, accents and punctuation removed, harbour words dropped."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"[^a-z0-9 ]+", " ", plain)
    plain = _NOISE.sub(" ", plain)
    return " ".join(plain.split())


def place_code(text: str) -> str | None:
    """The UN/LOCODE or IATA code a place is written as, or known by.

    Args:
        text: For example `AEJEA`, `Jebel Ali (AEJEA)`, `Jebel Ali AE`, `DXB`, `Port of Chennai`.

    Returns:
        The code, or None when the place is not recognised.
    """
    raw = text.strip()
    if not raw:
        return None
    bracketed = _BRACKETED.search(raw.upper())
    if bracketed:
        code = bracketed.group(1)
        return AIRPORTS.get(code, code)
    # Deals store an extracted place as "<code> <country>" ("INMAA IN", "DXB AE").
    tokens = re.split(r"[\s,]+", raw)
    if len(tokens) == 2 and re.fullmatch(r"[A-Z]{2}", tokens[1]):
        if _LOCODE.match(tokens[0]):
            return tokens[0]
        if _IATA.match(tokens[0]):
            return AIRPORTS.get(tokens[0], tokens[0])
    if raw.isupper() and _LOCODE.match(raw.replace(" ", "")):
        return raw.replace(" ", "")
    if raw.isupper() and _IATA.match(raw):
        return AIRPORTS.get(raw, raw)
    name = normalise_place(raw)
    if name in ALIASES:
        return ALIASES[name]
    # "Jebel Ali AE" / "Riyadh, Saudi Arabia": the longest known name inside wins.
    for alias in sorted(ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", name):
            return ALIASES[alias]
    return None


def place_key(text: str) -> str:
    """What two spellings of the same place have in common (the code, else the name)."""
    return place_code(text) or normalise_place(text)
