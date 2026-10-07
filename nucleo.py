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
    "automatico": {"velas": True, "alertas": True, "parte": True, "analisis": True},
    "analisis": {"noticias": True, "hora": "08:00"},   # daily AI analysis of the easy page (sala/analista.py)
    "historico": {
        "intervalos": [1, 60, 1440],     # OHLC tails downloaded each run: live 1-min, 30 days of hourly (cross-check), 2 years of daily
        "dias_trades": 90,               # how far back the Trades backfill looks for gaps
        "max_llamadas_panel": 300,       # cap for the panel button (~5 min); the terminal has no cap
        "volcar_cada": 50,               # Trades calls between two disk flushes
        "hueco_min_minutos": 60,         # a gap shorter than this is "minutes without trades", not a hole to backfill
    },
    "backtest": {
        "capital_inicial": 10000, "comision_pct": 0.40, "deslizamiento_pct": 0.05, "deslizamiento_stop_pct": 0.10,
        "minimo_orden_eur": 10, "riesgo_pct": 1.0, "tope_activo_pct": 30, "parada_dia_pct": 3, "apagado_pct": 12,
        "stop_min_pct": 0.3, "dia": "local",
        "ventana_is_dias": 180, "ventana_oos_dias": 60, "oos_min_dias": 30, "ventanas_minimas": 4,
        "min_operaciones_is": 30, "hueco_max_dias": 7, "semillas_azar": 200, "semillas_azar_panel": 50,
        "max_operaciones_guardadas": 5000,
        "puertas": {"expectativa_min": 0, "profit_factor_min": 1.3, "drawdown_max_pct": 20, "operaciones_min": 100,
                    "p_azar_max": 0.05, "consistencia_min": 0.5},
    },
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


COMENTARIOS_CONFIG = {   # the section comments config.yaml ships with; yaml.safe_dump would drop them on every panel toggle
    "historico": "# Histórico de Kraken: python app.py historico",
    "mercados_extra": "# Portada fácil: mercados tradicionales de Stooq (solo mirar). clave, nombre, simbolo, moneda, tipo",
    "backtest": "# Backtest: comisión taker de Kraken Pro 0,40 % (maker 0,25); cámbiala si operas con limitadas",
}


def guardar_config(cfg):
    texto = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=1000)
    lineas = []
    for linea in texto.splitlines():
        clave = linea.split(":", 1)[0] if linea and not linea[0].isspace() and ":" in linea else None
        if clave in COMENTARIOS_CONFIG:
            lineas.append(COMENTARIOS_CONFIG[clave])
        lineas.append(linea)
    CONFIG.write_text("# Configuración de la sala. Se edita desde el panel o a mano (los comentarios propios se pierden al tocar un interruptor).\n"
                      + "\n".join(lineas) + "\n", encoding="utf-8")


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
