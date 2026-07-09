"""
Motor de comparación de versiones de Pulse.

Cada servicio tiene un "modo" explícito de comparación. Esto evita las heurísticas
genéricas que fallan en herramientas como Watchtower/WUD:

- semver         -> comparación estándar de versiones (1.2.3 vs 1.3.0)
- semver_suffix  -> semver pero ignorando un sufijo de build conocido (ej. LinuxServer: -ls351)
- date           -> versionado por fecha (ej. v20260114-1008), se compara cronológicamente
- digest         -> la "versión instalada" es un hash SHA256 (imagen pinneada), se compara hash contra hash
- floating       -> tag flotante (latest, release, stable...). Se resuelve a un digest real
                     y se avisa cuando ese digest cambia, en vez de fiarse del texto del tag.
"""

import re
from packaging import version as pkg_version
from packaging.version import InvalidVersion

# Tags que no representan una versión fija, sino un puntero móvil
FLOATING_TAGS = {"latest", "release", "stable", "master", "main", "edge", "rolling", "nightly"}

# Patrones de sufijo conocidos por proveedor de imagen
SUFFIX_PATTERNS = {
    "linuxserver": r"-ls(\d+)$",
}

DATE_PATTERN = re.compile(r"^v?\d{8}-\d{3,4}$")
DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)

# Tags "familia": solo mayor, o mayor.menor, sin parche (ej. "8", "16", "v1.4").
# Los registries los mantienen apuntando siempre al último parche de esa rama,
# así que se comportan como un puntero móvil, no como una versión fija.
BARE_VERSION_PATTERN = re.compile(r"^v?\d+(\.\d+)?$")

# Sufijos que solo indican "canal de publicación", no cambian la versión real
_SUFIJOS_INOFENSIVOS = re.compile(r"-(stable|final)$", re.IGNORECASE)

# Códigos de prerelease reconocidos como fragmento completo tras separar por . - _
_CODIGOS_PRERELEASE = {"alpha", "beta", "rc", "dev", "development", "pre", "nightly", "canary", "preview", "snapshot", "a", "b"}

# Timestamp incrustado en cualquier parte del tag (ej. "10.11.11.20260606-abcd"),
# no solo cuando el tag es puramente numérico.
_EMBED_TIMESTAMP = re.compile(r"\d{8,}")


def elegir_mejor_semver(tags: list[str], patron_sufijo: str | None = None) -> str | None:
    """
    De una lista de tags, elige el de semver estable más alto. Ignora:
    - tags flotantes (latest, release...)
    - tags "canal"/puntero sin parche (ej. "v3", "8") -- no son una release fija
    - prereleases (beta, alpha, rc, formas cortas como -b.88)
    - tags con un timestamp incrustado (nightly builds), aunque lleven puntos

    Si el proveedor tiene un sufijo conocido (ej. LinuxServer: -lsXXX), se prefieren
    los tags que lo incluyen -- son el mismo build que un tag "pelado" equivalente,
    pero mantener el formato evita mostrar "2.7.6" junto a "v2.7.6-ls351" como si
    fueran cosas distintas cuando en realidad es la misma versión.
    """
    candidatos = tags
    if patron_sufijo:
        con_sufijo = [t for t in tags if re.search(patron_sufijo, t)]
        if con_sufijo:
            candidatos = con_sufijo

    mejor = None
    mejor_parsed = None
    for nombre in candidatos:
        if nombre.lower() in FLOATING_TAGS:
            continue
        if BARE_VERSION_PATTERN.match(nombre):
            continue
        if _EMBED_TIMESTAMP.search(nombre):
            continue
        if es_prerelease(nombre):
            continue
        limpio = normalizar_semver(nombre, patron_sufijo)
        try:
            parsed = pkg_version.parse(limpio)
        except InvalidVersion:
            continue
        if parsed.is_prerelease:
            continue
        if mejor_parsed is None or parsed > mejor_parsed:
            mejor_parsed = parsed
            mejor = nombre
    return mejor


def detectar_patron_sufijo(identificador: str) -> str | None:
    """Detecta si la imagen pertenece a un proveedor con sufijo de build conocido."""
    if identificador.startswith("linuxserver/") or "/linuxserver/" in identificador or "lscr.io" in identificador:
        return SUFFIX_PATTERNS["linuxserver"]
    return None


def es_prerelease(tag: str) -> bool:
    """True si el tag contiene un marcador de prerelease (beta, alpha, rc, -b.88...)."""
    partes = re.split(r"[-_.]", tag.lower())
    for p in partes:
        m = re.match(r"^([a-z]+)", p)
        if m and m.group(1) in _CODIGOS_PRERELEASE:
            return True
    return False


def detectar_modo(tag_instalado: str, identificador: str) -> tuple[str, str | None]:
    """
    Determina automáticamente el modo de comparación a partir del valor real
    instalado y del identificador de imagen/repo. Se usa al importar o añadir
    un servicio nuevo.
    """
    t = (tag_instalado or "").strip()

    if DIGEST_PATTERN.match(t) or t.startswith("sha256:"):
        return "digest", None

    if t.lower() in FLOATING_TAGS:
        return "floating", None

    if BARE_VERSION_PATTERN.match(t):
        # "8", "16", "v1.4"... son punteros de familia, se tratan como flotantes
        return "floating", None

    if DATE_PATTERN.match(t):
        return "date", None

    patron = detectar_patron_sufijo(identificador)
    if patron:
        return "semver_suffix", patron

    return "semver", None


def _quitar_v_inicial(tag: str) -> str:
    t = tag.strip()
    if len(t) > 1 and t[0].lower() == "v" and t[1].isdigit():
        return t[1:]
    return t


def normalizar_semver(tag: str, patron_sufijo: str | None = None) -> str:
    t = _quitar_v_inicial(tag)
    if patron_sufijo:
        t = re.sub(patron_sufijo, "", t)
    t = _SUFIJOS_INOFENSIVOS.sub("", t)
    return t


def _extraer_numero_sufijo(tag: str, patron_sufijo: str) -> int | None:
    """Extrae el número de build del sufijo (ej. 389 de '-ls389'), si el patrón lo captura."""
    m = re.search(patron_sufijo, tag)
    if m and m.groups():
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None
    return None


def comparar_semver(instalada: str, remota: str, patron_sufijo: str | None = None) -> str | None:
    """
    Devuelve 'green' si la instalada es igual o más nueva que la remota,
    'yellow' si hay una versión remota más nueva, o None si no se pudo parsear
    ninguna de las dos como semver (para que el llamante decida el fallback).

    Si la versión base es idéntica pero el sufijo lleva un número de build
    (ej. LinuxServer: -lsXXX) que ha subido, también se considera actualización
    -- el proveedor puede reconstruir la imagen sin tocar la versión de la app.
    """
    a = normalizar_semver(instalada, patron_sufijo)
    b = normalizar_semver(remota, patron_sufijo)
    try:
        va = pkg_version.parse(a)
        vb = pkg_version.parse(b)
    except InvalidVersion:
        return None

    if va > vb:
        return "green"
    if va < vb:
        return "yellow"

    if patron_sufijo:
        na = _extraer_numero_sufijo(instalada, patron_sufijo)
        nb = _extraer_numero_sufijo(remota, patron_sufijo)
        if na is not None and nb is not None and nb > na:
            return "yellow"

    return "green"


def comparar_fecha(instalada: str, remota: str) -> str | None:
    a_digits = re.sub(r"\D", "", instalada)
    b_digits = re.sub(r"\D", "", remota)
    if not a_digits or not b_digits:
        return None
    return "green" if a_digits >= b_digits else "yellow"


def comparar_digest(instalado: str, remoto: str) -> str | None:
    if not instalado or not remoto:
        return None
    return "green" if instalado.strip().lower() == remoto.strip().lower() else "yellow"


def elegir_mejor_fecha(tags: list[str]) -> str | None:
    candidatos = [t for t in tags if t.lower() not in FLOATING_TAGS]
    return max(candidatos, default=None)
