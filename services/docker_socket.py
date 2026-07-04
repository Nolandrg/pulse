"""
Habla con el Docker Engine API directamente por el socket Unix montado en
/var/run/docker.sock, sin depender del SDK oficial 'docker' (así nos ahorramos
esa dependencia extra: httpx ya soporta sockets Unix de forma nativa).
"""

import logging
import socket

import httpx

logger = logging.getLogger("pulse")

SOCKET_PATH = "/var/run/docker.sock"


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=SOCKET_PATH)
    return httpx.AsyncClient(transport=transport, base_url="http://docker")


def _separar_repo_tag(imagen: str) -> tuple[str, str]:
    imagen = imagen.split("@")[0]  # quitar digest si viniera pegado al nombre
    ultimo_segmento = imagen.rsplit("/", 1)[-1]
    if ":" in ultimo_segmento:
        base, tag = imagen.rsplit(":", 1)
        return base, tag
    return imagen, "latest"


async def listar_contenedores() -> list[dict]:
    """
    Devuelve una lista de contenedores en marcha con su nombre, imagen (repo:tag)
    y el digest real que tienen corriendo (para poder comparar de verdad más
    adelante, sobre todo en tags flotantes como 'latest').
    """
    resultado = []
    try:
        async with _client() as client:
            resp = await client.get("/containers/json?all=false", timeout=10.0)
            if resp.status_code != 200:
                logger.error(f"Docker socket respondió {resp.status_code} al listar contenedores")
                return resultado
            contenedores = resp.json()

            for c in contenedores:
                nombre = (c.get("Names") or ["/desconocido"])[0].lstrip("/")
                imagen = c.get("Image", "")
                image_id = c.get("ImageID", "")
                repo, tag = _separar_repo_tag(imagen)

                digest = None
                try:
                    inspect = await client.get(f"/images/{image_id}/json", timeout=10.0)
                    if inspect.status_code == 200:
                        repo_digests = inspect.json().get("RepoDigests") or []
                        if repo_digests:
                            digest = repo_digests[0].split("@")[-1]
                except Exception:
                    pass

                resultado.append({
                    "nombre": nombre,
                    "identificador": repo,
                    "tag": tag,
                    "digest": digest,
                })
    except Exception as e:
        logger.error(f"No se pudo acceder al socket de Docker: {e}")

    return resultado


async def obtener_info_por_nombre(nombre: str) -> dict | None:
    """Busca un contenedor en marcha cuyo nombre coincida exactamente."""
    contenedores = await listar_contenedores()
    for c in contenedores:
        if c["nombre"] == nombre:
            return c
    return None


async def obtener_propia_imagen() -> str | None:
    """
    Identifica la imagen del propio contenedor Pulse (leyendo su hostname,
    que Docker fija al ID corto del contenedor) para poder excluirse
    automáticamente al importar.
    """
    hostname = socket.gethostname()
    try:
        async with _client() as client:
            resp = await client.get(f"/containers/{hostname}/json", timeout=10.0)
            if resp.status_code == 200:
                imagen = resp.json().get("Config", {}).get("Image", "")
                repo, _tag = _separar_repo_tag(imagen)
                return repo
    except Exception as e:
        logger.error(f"No se pudo autoidentificar el contenedor de Pulse: {e}")
    return None
