import json
import os
import logging
from datetime import datetime
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services import version_engine as ve
from services import github_client, dockerhub_client, oci_client, docker_socket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pulse")

JSON_PATH = "/app/config/servicios.json"
TEMPLATES_DIR = "/app/templates"
STATIC_DIR = "/app/static"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN_ENV = os.environ.get("GITHUB_TOKEN")
DEFAULT_INTERVAL = int(os.environ.get("DEFAULT_INTERVAL", "30"))

scheduler = AsyncIOScheduler()


# ---------- Modelos ----------

class ServiceRequest(BaseModel):
    nombre: str
    tipo: str
    identificador: str
    version_instalada: str
    modo: str | None = None
    github_repo: str | None = None


class NombreRequest(BaseModel):
    nombre: str


class ProviderUpdate(BaseModel):
    nombre: str
    tipo: str


class InstalledUpdate(BaseModel):
    nombre: str
    version_instalada: str


class ConfigUpdate(BaseModel):
    intervalo: int
    hora_inicio: int
    hora_fin: int
    github_token: str | None = None


# ---------- Persistencia ----------

def cargar_datos() -> dict:
    default_data = {
        "configuracion_global": {
            "intervalo_minutos": DEFAULT_INTERVAL,
            "hora_inicio": 0,
            "hora_fin": 23,
            "github_token": None,
        },
        "servicios": [],
    }
    if not os.path.exists(JSON_PATH):
        return default_data
    with open(JSON_PATH, "r") as f:
        try:
            datos = json.load(f)
            datos.setdefault("configuracion_global", default_data["configuracion_global"])
            datos["configuracion_global"].setdefault("github_token", None)
            return datos
        except Exception:
            return default_data


def guardar_datos(datos: dict):
    os.makedirs(os.path.dirname(JSON_PATH), exist_ok=True)
    with open(JSON_PATH, "w") as f:
        json.dump(datos, f, indent=2)


def token_github(datos: dict) -> str | None:
    return datos["configuracion_global"].get("github_token") or GITHUB_TOKEN_ENV


# ---------- Notificaciones ----------

async def notificar_telegram(mensaje: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}, timeout=10.0)
        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")


# ---------- Selección de cliente por tipo/host ----------

def _cliente_docker(identificador: str):
    host = identificador.split("/")[0]
    if "." in host or host == "ghcr.io":
        return oci_client
    return dockerhub_client


# ---------- Verificación de un servicio ----------

async def _verificar_floating(s: dict, tipo: str, identificador: str, estado_previo: str | None) -> bool:
    """
    Para tags flotantes (latest, release, stable...) y tags de familia (8, 16...)
    NUNCA intentamos adivinar "el nombre de la próxima versión" -- eso es lo que
    causaba que Pulse devolviera builds de desarrollo/staging al azar. En su lugar,
    resolvemos el digest real del tag que YA tienes instalado y solo avisamos si
    ese digest cambia.
    """
    if tipo == "github":
        # No aplica: un repo de GitHub en modo flotante no tiene un "digest de imagen"
        s["estado_led"] = "green"
        s["version_remota"] = s["version_instalada"]
        return False

    tag_ref = s["version_instalada"]
    cliente = _cliente_docker(identificador)

    try:
        digest_remoto = await cliente.obtener_digest_tag(identificador, tag_ref)
    except Exception as e:
        logger.error(f"Error resolviendo digest flotante de {s['nombre']}: {e}")
        digest_remoto = None

    if digest_remoto is None:
        s["estado_led"] = "red"
        return False

    digest_anterior = s.get("digest_remoto")
    s["digest_remoto"] = digest_remoto

    if s.get("digest_instalado"):
        # Importado automáticamente: sabemos con certeza qué build corre ahora mismo
        cambio = digest_remoto != s["digest_instalado"]
    else:
        # Añadido a mano: solo podemos avisar cuando cambia respecto a la última
        # comprobación (igual que hacía el script original con Telegram)
        cambio = digest_anterior is not None and digest_remoto != digest_anterior

    s["estado_led"] = "yellow" if cambio else "green"
    s["version_remota"] = f"{tag_ref} (nuevo build)" if cambio else f"{tag_ref} (al día)"
    return s["estado_led"] == "yellow" and estado_previo != "yellow"


async def verificar_servicio(s: dict, gh_token: str | None, mapa_contenedores: dict | None = None) -> bool:
    """Devuelve True si el estado pasó a 'yellow' en esta comprobación (para notificar)."""
    if s.get("pausado"):
        return False

    identificador = s["identificador"]
    tipo = s.get("tipo", "docker")
    modo = s.get("modo") or "semver"
    patron_sufijo = s.get("patron_sufijo")

    # Sincroniza con el contenedor real en marcha, si existe uno con este nombre.
    # Así, si el usuario actualiza el contenedor por su cuenta (o con Portainer,
    # Dockge, etc.), Pulse deja de comparar contra un dato manual desactualizado.
    if tipo == "docker" and mapa_contenedores:
        info_actual = mapa_contenedores.get(s["nombre"])
        if info_actual:
            if info_actual.get("tag") and info_actual["tag"] != s["version_instalada"]:
                s["version_instalada"] = info_actual["tag"]
            if info_actual.get("digest"):
                s["digest_instalado"] = info_actual["digest"]

    estado_previo = s.get("estado_led")

    if modo == "floating":
        return await _verificar_floating(s, tipo, identificador, estado_previo)

    try:
        if tipo == "github":
            nueva = await github_client.resolver_version_mas_reciente(
                identificador, modo, patron_sufijo, token=gh_token
            )
        else:
            cliente = _cliente_docker(identificador)
            nueva = await cliente.resolver_version_mas_reciente(identificador, modo, patron_sufijo)
    except Exception as e:
        logger.error(f"Error verificando {s['nombre']}: {e}")
        nueva = None

    if nueva is None:
        s["estado_led"] = "red"
        return False

    s["version_remota"] = nueva

    if modo in ("semver", "semver_suffix"):
        resultado = ve.comparar_semver(s["version_instalada"], nueva, patron_sufijo)
    elif modo == "date":
        resultado = ve.comparar_fecha(s["version_instalada"], nueva)
    elif modo == "digest":
        resultado = ve.comparar_digest(s["version_instalada"], nueva)
    else:
        resultado = None

    s["estado_led"] = resultado if resultado else "red"
    return s["estado_led"] == "yellow" and estado_previo != "yellow"


def dentro_de_horario(conf: dict) -> bool:
    ahora = datetime.now().hour
    inicio = conf.get("hora_inicio", 0)
    fin = conf.get("hora_fin", 23)
    if inicio <= fin:
        return inicio <= ahora <= fin
    return ahora >= inicio or ahora <= fin  # rango que cruza medianoche


async def _mapa_contenedores_actuales() -> dict:
    try:
        contenedores = await docker_socket.listar_contenedores()
        return {c["nombre"]: c for c in contenedores}
    except Exception as e:
        logger.error(f"No se pudo listar contenedores para sincronizar versiones: {e}")
        return {}


async def check_updates_task():
    datos = cargar_datos()
    if not dentro_de_horario(datos["configuracion_global"]):
        return

    gh_token = token_github(datos)
    mapa_contenedores = await _mapa_contenedores_actuales()
    avisos = []

    for s in datos.get("servicios", []):
        try:
            hubo_cambio = await verificar_servicio(s, gh_token, mapa_contenedores)
            if hubo_cambio:
                avisos.append(f"🔔 {s['nombre']}: nueva versión disponible ({s.get('version_remota')})")
        except Exception as e:
            logger.error(f"Fallo comprobando {s.get('nombre')}: {e}")
            s["estado_led"] = "red"

    guardar_datos(datos)

    for aviso in avisos:
        await notificar_telegram(aviso)


# ---------- Ciclo de vida / scheduler ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    datos_iniciales = cargar_datos()
    intervalo = datos_iniciales["configuracion_global"].get("intervalo_minutos", DEFAULT_INTERVAL)
    scheduler.add_job(check_updates_task, "interval", minutes=intervalo, id="check_updates")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Pulse", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------- Enlace a la página de origen (para el nombre del servicio) ----------

def construir_url_fuente(s: dict) -> str:
    tipo = s.get("tipo", "docker")
    identificador = s["identificador"]

    # Si conocemos el repo de GitHub (guardado explícitamente, o porque el propio
    # tipo de comprobación ya es GitHub), enlazamos ahí -- es donde se ve el
    # historial de cambios real, aunque la comprobación de versión se haga
    # contra Docker Hub/GHCR.
    github_repo = s.get("github_repo") or (identificador if tipo == "github" else None)
    if github_repo:
        return f"https://github.com/{github_repo}"

    host = identificador.split("/")[0]
    if "." not in host:
        return f"https://hub.docker.com/r/{identificador}/tags"
    if host == "ghcr.io":
        partes = identificador.split("/")
        org = partes[1] if len(partes) > 1 else partes[0]
        return f"https://github.com/orgs/{org}/packages"
    if host == "quay.io":
        repo = identificador.split("/", 1)[1] if "/" in identificador else identificador
        return f"https://quay.io/repository/{repo}?tab=tags"
    return f"https://{identificador}"


# ---------- Rutas ----------

@app.get("/", response_class=HTMLResponse)
def leer_panel(request: Request):
    datos = cargar_datos()
    servicios = datos.get("servicios", [])
    for s in servicios:
        s["url_fuente"] = construir_url_fuente(s)
    return Jinja2Templates(directory=TEMPLATES_DIR).TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"servicios": servicios, "conf": datos["configuracion_global"]},
    )


@app.post("/api/add-service")
async def add_service(data: ServiceRequest):
    datos = cargar_datos()
    modo, patron = ve.detectar_modo(data.version_instalada, data.identificador)
    if data.modo:
        modo = data.modo

    datos["servicios"].append({
        "nombre": data.nombre,
        "tipo": data.tipo,
        "identificador": data.identificador,
        "version_instalada": data.version_instalada,
        "version_remota": "...",
        "modo": modo,
        "patron_sufijo": patron,
        "github_repo": data.github_repo or None,
        "digest_instalado": None,
        "digest_remoto": None,
        "estado_led": "red",
        "pausado": False,
    })
    guardar_datos(datos)
    return {"status": "ok"}


@app.post("/api/import-docker")
async def import_docker():
    datos = cargar_datos()
    existentes = {s["nombre"] for s in datos["servicios"]}

    propia_imagen = await docker_socket.obtener_propia_imagen()
    contenedores = await docker_socket.listar_contenedores()

    nuevos = 0
    for c in contenedores:
        if c["nombre"] in existentes:
            continue
        if propia_imagen and c["identificador"] == propia_imagen:
            # Es el propio Pulse: se omite automáticamente
            continue

        modo, patron = ve.detectar_modo(c["tag"], c["identificador"])

        datos["servicios"].append({
            "nombre": c["nombre"],
            "tipo": "docker",
            "identificador": c["identificador"],
            "version_instalada": c["tag"],
            "version_remota": "...",
            "modo": modo,
            "patron_sufijo": patron,
            "digest_instalado": c.get("digest"),
            "digest_remoto": None,
            "estado_led": "red",
            "pausado": False,
        })
        nuevos += 1

    guardar_datos(datos)
    return {"status": "ok", "nuevos": nuevos}


@app.post("/api/check-one")
async def check_one(data: NombreRequest):
    datos = cargar_datos()
    gh_token = token_github(datos)
    mapa_contenedores = await _mapa_contenedores_actuales()
    for s in datos.get("servicios", []):
        if s["nombre"] == data.nombre:
            hubo_cambio = await verificar_servicio(s, gh_token, mapa_contenedores)
            guardar_datos(datos)
            if hubo_cambio:
                await notificar_telegram(f"🔔 {s['nombre']}: nueva versión disponible ({s.get('version_remota')})")
            return {"status": "ok"}
    return {"status": "no encontrado"}


@app.post("/api/update-provider")
async def update_provider(data: ProviderUpdate):
    datos = cargar_datos()
    for s in datos["servicios"]:
        if s["nombre"] == data.nombre:
            s["tipo"] = data.tipo
            s["version_remota"] = "..."
            s["estado_led"] = "red"
    guardar_datos(datos)
    return {"status": "ok"}


@app.post("/api/update-installed")
async def update_installed(data: InstalledUpdate):
    """
    Corrige manualmente la versión instalada. Necesario para servicios de tipo
    GitHub (programas instalados en el sistema) y para servicios Docker añadidos
    a mano que no coinciden con ningún contenedor real en marcha -- Pulse no
    tiene forma de saber por sí solo que los has actualizado.
    """
    datos = cargar_datos()
    gh_token = token_github(datos)
    for s in datos["servicios"]:
        if s["nombre"] == data.nombre:
            s["version_instalada"] = data.version_instalada
            modo, patron = ve.detectar_modo(data.version_instalada, s["identificador"])
            s["modo"] = modo
            s["patron_sufijo"] = patron
            s["digest_instalado"] = None
            # Verificamos al instante, en vez de dejarlo en rojo "pendiente"
            # hasta la próxima comprobación
            await verificar_servicio(s, gh_token)
    guardar_datos(datos)
    return {"status": "ok"}


@app.post("/api/update-config")
async def update_config(data: ConfigUpdate):
    datos = cargar_datos()
    datos["configuracion_global"]["intervalo_minutos"] = data.intervalo
    datos["configuracion_global"]["hora_inicio"] = data.hora_inicio
    datos["configuracion_global"]["hora_fin"] = data.hora_fin
    if data.github_token is not None:
        datos["configuracion_global"]["github_token"] = data.github_token or None
    guardar_datos(datos)

    try:
        scheduler.reschedule_job("check_updates", trigger="interval", minutes=data.intervalo)
    except Exception as e:
        logger.error(f"No se pudo reprogramar el intervalo: {e}")

    return {"status": "ok"}


@app.post("/api/delete-service")
def delete(data: NombreRequest):
    datos = cargar_datos()
    datos["servicios"] = [s for s in datos["servicios"] if s["nombre"] != data.nombre]
    guardar_datos(datos)
    return {"status": "ok"}


@app.post("/api/toggle-pause")
def toggle_pause(data: NombreRequest):
    datos = cargar_datos()
    for s in datos["servicios"]:
        if s["nombre"] == data.nombre:
            s["pausado"] = not s.get("pausado", False)
    guardar_datos(datos)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
