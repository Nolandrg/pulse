"""
Cliente OCI genérico para Pulse.

Cubre cualquier registro que hable el protocolo estándar OCI Distribution:
GHCR (ghcr.io), Quay (quay.io), lscr.io, etc. Se usa cuando el identificador
de la imagen trae un host explícito (ej. "ghcr.io/immich-app/immich-server").
"""

import asyncio
import ipaddress
import logging
import re
import socket
import httpx

from . import version_engine as ve

logger = logging.getLogger("pulse")

_AUTH_URLS = {
    "ghcr.io": "https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
    "quay.io": "https://quay.io/v2/auth?service=quay.io&scope=repository:{repo}:pull",
    "lscr.io": "https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
}

# Pulse no pretende ser un cliente OCI genérico. Limitar los registros a los
# que soportamos evita que un identificador introducido desde el panel se
# convierta en una petición HTTPS a un host elegido por el usuario.
REGISTROS_OCI_PERMITIDOS = frozenset(_AUTH_URLS)
_COMPONENTE_REPOSITORIO = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")

_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def validar_identificador_oci(identificador: str) -> tuple[str, str]:
    """Valida y separa un identificador OCI de un registro permitido."""
    partes = identificador.split("/", 1)
    if len(partes) != 2:
        raise ValueError("Un identificador OCI debe incluir registro y repositorio")

    host, repositorio = partes
    if host not in REGISTROS_OCI_PERMITIDOS:
        raise ValueError("El registro OCI no está permitido")

    componentes = repositorio.split("/")
    if not componentes or any(not _COMPONENTE_REPOSITORIO.fullmatch(c) for c in componentes):
        raise ValueError("El repositorio OCI tiene un formato inválido")
    return host, repositorio


def es_identificador_oci(identificador: str) -> bool:
    try:
        validar_identificador_oci(identificador)
    except ValueError:
        return False
    return True


async def validar_destino_oci(host: str) -> None:
    """Impide conexiones OCI a destinos no globales, incluso tras DNS."""
    if host not in REGISTROS_OCI_PERMITIDOS:
        raise ValueError("El registro OCI no está permitido")

    try:
        resultados = await asyncio.to_thread(
            socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise ValueError("No se pudo resolver el registro OCI") from error

    direcciones = {resultado[4][0] for resultado in resultados}
    if not direcciones:
        raise ValueError("El registro OCI no devolvió direcciones")

    for direccion in direcciones:
        try:
            ip = ipaddress.ip_address(direccion)
        except ValueError as error:
            raise ValueError("El registro OCI devolvió una dirección inválida") from error
        if not ip.is_global:
            raise ValueError("El registro OCI resolvió a una red no permitida")


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
    host, repo = validar_identificador_oci(identificador)
    await validar_destino_oci(host)
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
    host, repo = validar_identificador_oci(identificador)
    await validar_destino_oci(host)
    token = await _obtener_token(host, repo)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://{host}/v2/{repo}/tags/list?n={limite}"
    tags = []
    urls_visitadas = set()

    async with httpx.AsyncClient() as client:
        try:
            while url and url not in urls_visitadas:
                urls_visitadas.add(url)

                resp = await client.get(url, headers=headers, timeout=10.0)
                if resp.status_code != 200:
                    break

                tags.extend(resp.json().get("tags") or [])

                siguiente = None
                link = resp.headers.get("Link", "")
                for enlace in link.split(","):
                    if 'rel="next"' in enlace:
                        inicio = enlace.find("<")
                        fin = enlace.find(">", inicio + 1)
                        if inicio != -1 and fin != -1:
                            siguiente = enlace[inicio + 1:fin]
                        break

                if siguiente:
                    if siguiente.startswith("/"):
                        url = f"https://{host}{siguiente}"
                    else:
                        url = siguiente
                else:
                    url = None

        except Exception as e:
            logger.error(f"Error listando tags OCI {identificador}: {e}")
            return []

    return tags[:limite]




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
