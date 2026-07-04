"""
Cliente de GitHub para Pulse.

Punto clave: NO nos fiamos ciegamente de /releases/latest para decidir "la versión más
nueva", porque ese endpoint devuelve la release marcada como "Latest" por GitHub
(normalmente la más reciente publicada), que puede no ser la de mayor versión semántica
si un mantenedor publica un parche a una rama antigua después de una versión mayor.

Para modos semver, pedimos la lista de tags y elegimos nosotros el semver más alto.
Para el resto de modos, usamos /releases/latest como aproximación razonable.
"""

import logging
import httpx

from . import version_engine as ve

logger = logging.getLogger("pulse")

GITHUB_API = "https://api.github.com"


def _headers(token: str | None) -> dict:
    h = {"User-Agent": "Pulse-Dashboard", "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def obtener_tags(repo: str, token: str | None = None, limite: int = 50) -> list[str]:
    url = f"{GITHUB_API}/repos/{repo}/tags?per_page={limite}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=_headers(token), timeout=10.0)
            if resp.status_code == 200:
                return [t["name"] for t in resp.json()]
            if resp.status_code == 403:
                logger.warning(f"GitHub: límite de peticiones alcanzado consultando {repo}")
            elif resp.status_code == 404:
                logger.warning(f"GitHub: repo no encontrado {repo}")
        except Exception as e:
            logger.error(f"Error GitHub tags {repo}: {e}")
    return []


async def obtener_ultima_release(repo: str, token: str | None = None) -> str | None:
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=_headers(token), timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("tag_name")
            if resp.status_code == 403:
                logger.warning(f"GitHub: límite de peticiones alcanzado consultando {repo}")
        except Exception as e:
            logger.error(f"Error GitHub release {repo}: {e}")
    return None


async def resolver_version_mas_reciente(
    repo: str, modo: str, patron_sufijo: str | None, token: str | None = None
) -> str | None:
    if modo in ("semver", "semver_suffix"):
        tags = await obtener_tags(repo, token=token)
        mejor = ve.elegir_mejor_semver(tags, patron_sufijo)
        if mejor:
            return mejor
        # Ningún tag se pudo interpretar como semver: recurrimos a la release marcada
        return await obtener_ultima_release(repo, token=token)

    if modo == "date":
        tags = await obtener_tags(repo, token=token)
        mejor = ve.elegir_mejor_fecha(tags)
        if mejor:
            return mejor
        return await obtener_ultima_release(repo, token=token)

    return await obtener_ultima_release(repo, token=token)
