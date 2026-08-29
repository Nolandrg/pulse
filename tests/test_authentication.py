import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.staticfiles import StaticFiles as RealStaticFiles
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _static_files_without_local_app_directory(*args, **kwargs):
    """Permite importar la aplicación fuera de la ruta /app usada en Docker."""
    kwargs["check_dir"] = False
    return RealStaticFiles(*args, **kwargs)


def cargar_app(usuario="pulse", contrasena="prueba-segura"):
    """Importa una instancia nueva de Pulse con autenticación de prueba."""
    with patch.dict(os.environ, {
        "PULSE_AUTH_USER": usuario,
        "PULSE_AUTH_PASSWORD": contrasena,
    }, clear=False), patch("fastapi.staticfiles.StaticFiles", _static_files_without_local_app_directory):
        sys.modules.pop("main", None)
        app_module = importlib.import_module("main")
    app_module.TEMPLATES_DIR = str(PROJECT_ROOT / "templates")
    return app_module


def cabecera_basic(usuario="pulse", contrasena="prueba-segura"):
    valor = base64.b64encode(f"{usuario}:{contrasena}".encode()).decode()
    return {"Authorization": f"Basic {valor}"}


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.directorio_temporal = tempfile.TemporaryDirectory()
        self.json_path = Path(self.directorio_temporal.name) / "servicios.json"
        self.json_path.write_text(json.dumps({
            "configuracion_global": {
                "intervalo_minutos": 30,
                "hora_inicio": 0,
                "hora_fin": 23,
                "github_token": "token-secreto-de-prueba",
            },
            "servicios": [],
        }))
        self.main = cargar_app()
        self.main.JSON_PATH = str(self.json_path)

    def tearDown(self):
        self.directorio_temporal.cleanup()
        sys.modules.pop("main", None)

    def test_rechaza_peticiones_sin_credenciales_y_acepta_credenciales_validas(self):
        with TestClient(self.main.app) as client:
            respuesta_sin_credenciales = client.get("/")
            self.assertEqual(respuesta_sin_credenciales.status_code, 401)
            self.assertEqual(respuesta_sin_credenciales.headers["www-authenticate"], 'Basic realm="Pulse"')

            respuesta_autenticada = client.get("/", headers=cabecera_basic())
            self.assertEqual(respuesta_autenticada.status_code, 200)

    def test_el_token_no_se_envia_en_el_html(self):
        with TestClient(self.main.app) as client:
            respuesta = client.get("/", headers=cabecera_basic())
        self.assertNotIn("token-secreto-de-prueba", respuesta.text)
        self.assertIn("Hay un token configurado", respuesta.text)

    def test_campo_vacio_conserva_token_y_borrado_explicito_lo_elimina(self):
        datos = {"intervalo": 30, "hora_inicio": 0, "hora_fin": 23, "github_token": ""}
        with TestClient(self.main.app) as client:
            respuesta = client.post("/api/update-config", headers=cabecera_basic(), json=datos)
            self.assertEqual(respuesta.status_code, 200)
            persistido = json.loads(self.json_path.read_text())
            self.assertEqual(
                persistido["configuracion_global"]["github_token"],
                "token-secreto-de-prueba",
            )

            datos["clear_github_token"] = True
            respuesta = client.post("/api/update-config", headers=cabecera_basic(), json=datos)
            self.assertEqual(respuesta.status_code, 200)
            persistido = json.loads(self.json_path.read_text())
            self.assertIsNone(persistido["configuracion_global"]["github_token"])


class StartupConfigurationTests(unittest.TestCase):
    def test_requiere_contrasena_al_iniciar(self):
        with patch.dict(os.environ, {"PULSE_AUTH_PASSWORD": ""}, clear=False), patch(
            "fastapi.staticfiles.StaticFiles", _static_files_without_local_app_directory
        ):
            sys.modules.pop("main", None)
            app_module = importlib.import_module("main")

        with self.assertRaisesRegex(RuntimeError, "PULSE_AUTH_PASSWORD"):
            with TestClient(app_module.app):
                pass
        sys.modules.pop("main", None)
