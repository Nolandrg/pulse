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


async def obtener_tags_paginado(identificador: str, limite: int = 50, max_paginas: int = 5):
    """
    Generador que va pidiendo páginas de tags (orden nativo de la API: last_updated
    descendente) según se necesiten, sin traer de golpe más de lo necesario.

    Repos muy activos (ej. jellyfin/jellyfin, con builds nightly/rc multi-arquitectura
    en cada push) pueden empujar el tag estable real fuera de la primera página de 50
    solo por volumen de publicaciones recientes. El llamante decide cuándo ha visto
    ya suficiente y deja de iterar.
    """
    repo = _normalizar_repo(identificador)
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size={limite}&ordering=last_updated"
    async with httpx.AsyncClient() as client:
        for _ in range(max_paginas):
            if not url:
                return
            try:
                resp = await client.get(url, timeout=10.0)
                if resp.status_code != 200:
                    logger.warning(f"Docker Hub respondió {resp.status_code} para {identificador}")
                    return
                body = resp.json()
                data = body.get("results", [])
                yield [
                    {"name": t["name"], "digest": t.get("digest"), "last_pushed": t.get("tag_last_pushed")}
                    for t in data
                ]
                url = body.get("next")
            except Exception as e:
                logger.error(f"Error Docker Hub tags {identificador}: {e}")
                return


async def obtener_tags(identificador: str, limite: int = 50) -> list[dict]:
    """Compatibilidad: solo la primera página (usado por obtener_digest_tag y similares)."""
    async for pagina in obtener_tags_paginado(identificador, limite, max_paginas=1):
        return pagina
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

    Va acumulando páginas de tags (más recientes primero) y prueba a resolver
    tras cada una -- así los repos normales resuelven con una sola petición,
    y solo los repos muy activos (donde el tag estable puede quedar fuera de
    los primeros 50 por volumen de publicaciones) llegan a pedir páginas extra.
    """
    if modo not in ("semver", "semver_suffix", "date"):
        return None

    nombres_acumulados: list[str] = []
    async for pagina in obtener_tags_paginado(identificador):
        nombres_acumulados.extend(t["name"] for t in pagina)

        if modo in ("semver", "semver_suffix"):
            candidato = ve.elegir_mejor_semver(nombres_acumulados, patron_sufijo)
        else:
            candidato = ve.elegir_mejor_fecha(nombres_acumulados)

        if candidato is not None:
            return candidato

    return None
