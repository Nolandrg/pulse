"""
Cliente OCI genérico para Pulse.

Cubre cualquier registro que hable el protocolo estándar OCI Distribution:
GHCR (ghcr.io), Quay (quay.io), lscr.io, etc. Se usa cuando el identificador
de la imagen trae un host explícito (ej. "ghcr.io/immich-app/immich-server").
"""

import logging
import httpx

from . import version_engine as ve

logger = logging.getLogger("pulse")

_AUTH_URLS = {
    "ghcr.io": "https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
    "quay.io": "https://quay.io/v2/auth?service=quay.io&scope=repository:{repo}:pull",
    "lscr.io": "https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
}

_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def _parse_identificador(identificador: str) -> tuple[str, str]:
    """Separa 'ghcr.io/immich-app/immich-server' en (host, repo)."""
    partes = identificador.split("/", 1)
    if len(partes) == 2 and ("." in partes[0] or ":" in partes[0]):
        return partes[0], partes[1]
    # No debería llegar aquí si el enrutado en main.py es correcto
    return "ghcr.io", identificador


async def _obtener_token(host: str, repo: str) -> str | None:
    plantilla = _AUTH_URLS.get(host)
    if not plantilla:
        return None
    url = plantilla.format(repo=repo)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                return resp.json().get("token")
        except Exception as e:
            logger.error(f"Error token OCI {host}/{repo}: {e}")
    return None


async def obtener_digest_tag(identificador: str, tag: str) -> str | None:
    host, repo = _parse_identificador(identificador)
    token = await _obtener_token(host, repo)
    headers = {"Accept": _MANIFEST_ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://{host}/v2/{repo}/manifests/{tag}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.head(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return resp.headers.get("Docker-Content-Digest")
        except Exception as e:
            logger.error(f"Error digest OCI {identificador}:{tag} - {e}")
    return None


async def obtener_tags(identificador: str, limite: int = 50) -> list[str]:
    host, repo = _parse_identificador(identificador)
    token = await _obtener_token(host, repo)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://{host}/v2/{repo}/tags/list"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return (resp.json().get("tags") or [])[:limite]
        except Exception as e:
            logger.error(f"Error listando tags OCI {identificador}: {e}")
    return []


async def resolver_version_mas_reciente(identificador: str, modo: str, patron_sufijo: str | None) -> str | None:
    """
    Nota: el modo 'floating' NO pasa por aquí -- se resuelve por digest
    directamente en main.py (ver _verificar_floating), porque GHCR/OCI no
    garantiza ningún orden significativo en la lista de tags: coger "el primero"
    podía devolver perfectamente una build de staging o desarrollo.
    """
    tags = await obtener_tags(identificador)
    if not tags:
        return None

    if modo in ("semver", "semver_suffix"):
        return ve.elegir_mejor_semver(tags, patron_sufijo)

    if modo == "date":
        return ve.elegir_mejor_fecha(tags)

    return None