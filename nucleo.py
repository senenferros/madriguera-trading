"""Shared basics for the CLI and the panel: folders, config.yaml, the .env file and the HTTP timeout.

Nothing here touches money: the whole app reads public market data and keeps a simulated journal.
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DATOS_DIR = ROOT / "datos"
CONFIG = ROOT / "config.yaml"
ENV = ROOT / ".env"
TIMEOUT = 15

CONFIG_POR_DEFECTO = {
    "nombre": "La Madriguera Trading",
    "pares": ["XBTEUR", "ETHEUR"],
    "intervalo_minutos": 1,
    "hora_parte": "08:00",
    "alertas": {"volumen_x": 3, "enfriamiento_minutos": 30, "minimo_velas_dia": 30},
    "conservar_dias": 400,
    "automatico": {"velas": True, "alertas": True, "parte": True},
}


def cargar_env(ruta=None):
    """Load KEY=value lines from .env into os.environ (no python-dotenv needed). Missing file: nothing happens."""
    ruta = Path(ruta or ENV)
    valores = {}
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return valores
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave, valor = clave.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        if clave:
            valores[clave] = valor
            os.environ[clave] = valor
    return valores


def telegram_credenciales():
    cargar_env()
    return os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT", "").strip()


def _fusionar(base, extra):
    salida = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(salida.get(k), dict):
            salida[k] = _fusionar(salida[k], v)
        elif v is not None:
            salida[k] = v
    return salida


def cargar_config():
    """config.yaml on top of the defaults, so a half-written file still gives a complete config."""
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            datos = yaml.safe_load(fh) or {}
    except OSError:
        datos = {}
    if not isinstance(datos, dict):
        datos = {}
    return _fusionar(CONFIG_POR_DEFECTO, datos)


def guardar_config(cfg):
    texto = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=1000)
    CONFIG.write_text("# Configuración de la sala. Se edita desde el panel o a mano.\n" + texto, encoding="utf-8")


def automatico(cfg, tarea):
    """Is this automatic job switched on? (all on unless the owner paused it)"""
    return (cfg.get("automatico") or {}).get(tarea, True) is not False


def cambiar_automatico(tarea, activo):
    cfg = cargar_config()
    auto = dict(cfg.get("automatico") or {})
    auto[tarea] = bool(activo)
    cfg["automatico"] = auto
    guardar_config(cfg)
    return cfg


class Informe:
    """Health-check report shared by the CLI (printed) and the 'Comprobar' page."""

    def __init__(self, imprimir=False):
        self.imprimir = imprimir
        self.errores = 0
        self.secciones = []
        self.fecha = ""

    def seccion(self, titulo):
        self.secciones.append({"titulo": titulo, "items": []})
        if self.imprimir:
            print(titulo)

    def _add(self, estado, nombre, detalle):
        self.secciones[-1]["items"].append({"estado": estado, "nombre": nombre, "detalle": detalle})
        if self.imprimir:
            print(f"  [{estado.upper()}]".ljust(10) + f"{nombre} {detalle}".rstrip())

    def ok(self, nombre, detalle=""):
        self._add("ok", nombre, detalle)

    def falta(self, nombre, detalle=""):
        self.errores += 1
        self._add("falta", nombre, detalle)

    def error(self, nombre, detalle=""):
        self.errores += 1
        self._add("error", nombre, detalle)

    def aviso(self, nombre, detalle=""):
        self._add("aviso", nombre, detalle)
