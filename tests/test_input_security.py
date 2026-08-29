import base64
import importlib
import json
import os
import socket
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from fastapi.staticfiles import StaticFiles as RealStaticFiles
from fastapi.testclient import TestClient

from services import oci_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _static_files_without_local_app_directory(*args, **kwargs):
    kwargs["check_dir"] = False
    return RealStaticFiles(*args, **kwargs)


def cargar_app():
    with patch.dict(os.environ, {
        "PULSE_AUTH_USER": "pulse",
        "PULSE_AUTH_PASSWORD": "prueba-segura",
    }, clear=False), patch("fastapi.staticfiles.StaticFiles", _static_files_without_local_app_directory):
        sys.modules.pop("main", None)
        app_module = importlib.import_module("main")
    app_module.TEMPLATES_DIR = str(PROJECT_ROOT / "templates")
    return app_module


def cabecera_basic():
    valor = base64.b64encode(b"pulse:prueba-segura").decode()
    return {"Authorization": f"Basic {valor}"}


class _AtributosHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.atributos = []

    def handle_starttag(self, tag, attrs):
        self.atributos.extend(nombre for nombre, _valor in attrs)


class InputSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directorio_temporal = tempfile.TemporaryDirectory()
        self.json_path = Path(self.directorio_temporal.name) / "servicios.json"
        self.json_path.write_text(json.dumps({
            "configuracion_global": {
                "intervalo_minutos": 30,
                "hora_inicio": 0,
                "hora_fin": 23,
                "github_token": None,
            },
            "servicios": [],
        }))
        self.main = cargar_app()
        self.main.JSON_PATH = str(self.json_path)

    def tearDown(self):
        self.directorio_temporal.cleanup()
        sys.modules.pop("main", None)

    def datos_servicio(self, **cambios):
        datos = {
            "nombre": "Jellyfin",
            "tipo": "docker",
            "identificador": "jellyfin/jellyfin",
            "version_instalada": "10.10.0",
        }
        datos.update(cambios)
        return datos

    def test_datos_hostiles_se_renderizan_como_datos_y_no_como_handlers(self):
        nombre = "malicioso' onclick='alert(1)"
        version = '\"><img src=x onerror=alert(1)>'
        datos = json.loads(self.json_path.read_text())
        datos["servicios"] = [{
            "nombre": nombre,
            "tipo": "docker",
            "identificador": "jellyfin/jellyfin",
            "version_instalada": version,
            "version_remota": version,
            "modo": "semver",
            "estado_led": "red",
            "pausado": False,
        }]
        self.json_path.write_text(json.dumps(datos))

        with TestClient(self.main.app) as client:
            respuesta = client.get("/", headers=cabecera_basic())

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("<img src=x onerror=alert(1)>", respuesta.text)
        self.assertIn('data-action="check-one"', respuesta.text)
        parser = _AtributosHTML()
        parser.feed(respuesta.text)
        self.assertNotIn("onclick", parser.atributos)
        self.assertNotIn("onerror", parser.atributos)

    def test_rechaza_valores_de_configuracion_fuera_de_limites_sin_persistirlos(self):
        original = self.json_path.read_text()
        casos_invalidos = [
            {"intervalo": 0, "hora_inicio": 0, "hora_fin": 23},
            {"intervalo": 10081, "hora_inicio": 0, "hora_fin": 23},
            {"intervalo": 30, "hora_inicio": -1, "hora_fin": 23},
            {"intervalo": 30, "hora_inicio": 0, "hora_fin": 24},
        ]
        with TestClient(self.main.app) as client:
            for datos in casos_invalidos:
                respuesta = client.post("/api/update-config", headers=cabecera_basic(), json=datos)
                self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.json_path.read_text(), original)

    def test_valida_proveedores_identificadores_y_nombres_duplicados(self):
        with TestClient(self.main.app) as client:
            respuesta = client.post("/api/add-service", headers=cabecera_basic(), json=self.datos_servicio())
            self.assertEqual(respuesta.status_code, 200)

            duplicado = client.post(
                "/api/add-service", headers=cabecera_basic(),
                json=self.datos_servicio(nombre="jellyfin"),
            )
            self.assertEqual(duplicado.status_code, 409)

            for datos in (
                self.datos_servicio(nombre="<script>alert(1)</script>"),
                self.datos_servicio(version_instalada="1.0\nInjected"),
                self.datos_servicio(tipo="inexistente"),
                self.datos_servicio(identificador="registry.example/repo"),
                self.datos_servicio(identificador="localhost/repo"),
                self.datos_servicio(identificador="127.0.0.1/repo"),
                self.datos_servicio(tipo="github", identificador="sin-separador"),
            ):
                respuesta = client.post("/api/add-service", headers=cabecera_basic(), json=datos)
                self.assertEqual(respuesta.status_code, 422, datos)

            ghcr = client.post(
                "/api/add-service", headers=cabecera_basic(),
                json=self.datos_servicio(
                    nombre="Immich", identificador="ghcr.io/immich-app/immich-server"
                ),
            )
            self.assertEqual(ghcr.status_code, 200)

            github = client.post(
                "/api/add-service", headers=cabecera_basic(),
                json=self.datos_servicio(
                    nombre="Aplicacion GitHub", tipo="github", identificador="owner/repo",
                    github_repo="owner/repo",
                ),
            )
            self.assertEqual(github.status_code, 200)

            proveedor_invalido = client.post(
                "/api/update-provider", headers=cabecera_basic(),
                json={"nombre": "Jellyfin", "tipo": "inexistente"},
            )
            self.assertEqual(proveedor_invalido.status_code, 422)


class OciSsrfTests(unittest.IsolatedAsyncioTestCase):
    def test_rechaza_hosts_no_permitidos_ips_y_localhost(self):
        for identificador in (
            "localhost/repo",
            "127.0.0.1/repo",
            "10.0.0.1/repo",
            "169.254.169.254/repo",
            "[::1]/repo",
            "registry.example/repo",
        ):
            with self.assertRaises(ValueError, msg=identificador):
                oci_client.validar_identificador_oci(identificador)

    async def test_rechaza_destinos_privados_loopback_y_reservados_despues_de_dns(self):
        for direccion in ("127.0.0.1", "10.0.0.4", "169.254.169.254", "::1", "192.0.2.10"):
            with patch("services.oci_client.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (direccion, 443))
            ]):
                with self.assertRaises(ValueError, msg=direccion):
                    await oci_client.validar_destino_oci("ghcr.io")

    async def test_acepta_un_registro_permitido_resuelto_a_ip_global(self):
        with patch("services.oci_client.socket.getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
        ]):
            await oci_client.validar_destino_oci("ghcr.io")

