"""
Cliente de Docker Hub para Pulse.

Usa la API web pública de Docker Hub (hub.docker.com/v2), que no requiere
autenticación para repositorios públicos. Se usa para cualquier imagen sin
host de registro explícito en el identificador (ej. "linuxserver/heimdall",
"portainer/portainer-ce").
"""

import logging
import httpx

from . import version_engine as ve

logger = logging.getLogger("pulse")


def _normalizar_repo(identificador: str) -> str:
    """Las imágenes oficiales (sin namespace) viven bajo 'library/' en la API."""
    if "/" not in identificador:
        return f"library/{identificador}"
    return identificador


async def obtener_tags(identificador: str, limite: int = 50) -> list[dict]:
    """Lista de tags ordenados por fecha de push (orden nativo de la API, no semver)."""
    repo = _normalizar_repo(identificador)
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size={limite}&ordering=last_updated"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json().get("results", [])
                return [
                    {"name": t["name"], "digest": t.get("digest"), "last_pushed": t.get("tag_last_pushed")}
                    for t in data
                ]
            logger.warning(f"Docker Hub respondió {resp.status_code} para {identificador}")
        except Exception as e:
            logger.error(f"Error Docker Hub tags {identificador}: {e}")
    return []


async def obtener_digest_tag(identificador: str, tag: str) -> str | None:
    repo = _normalizar_repo(identificador)
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}/"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("digest")
        except Exception as e:
            logger.error(f"Error Docker Hub digest {identificador}:{tag} - {e}")
    return None


async def resolver_version_mas_reciente(identificador: str, modo: str, patron_sufijo: str | None) -> str | None:
    """
    Nota: el modo 'floating' NO pasa por aquí -- se resuelve por digest
    directamente en main.py, para no arriesgarnos a devolver una build de
    desarrollo/staging al no haber un orden fiable entre tags flotantes.
    """
    tags_info = await obtener_tags(identificador)
    if not tags_info:
        return None
    nombres = [t["name"] for t in tags_info]

    if modo in ("semver", "semver_suffix"):
        return ve.elegir_mejor_semver(nombres, patron_sufijo)

    if modo == "date":
        return ve.elegir_mejor_fecha(nombres)

    return None
