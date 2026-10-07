"""The seven people of the room, with a persona each, created once and saved to datos/equipo.yaml so the owner can
rename them. Names and colours are deterministic (a hash of the room name), like the office of the shorts studio."""
import hashlib

import yaml

import nucleo

ROLES = [
    ("vigia", "Vigía de mercado", "Mira los precios cada minuto y avisa cuando pasa algo. No se fía de nada que no venga con un dato."),
    ("cuant", "Analista cuantitativo", "Prueba cada estrategia contra el histórico antes de creérsela. Desconfía de lo que solo funciona en un periodo."),
    ("operador", "Operador en simulación", "Lleva las operaciones simuladas y anota cada una con su motivo. Nunca toca dinero real."),
    ("riesgo", "Gestor de riesgo", "Aplica las reglas: 1 % por operación, siempre stop, −3 % al día y se para, −12 % y se apaga todo. Es quien dice que no."),
    ("datos", "Documentalista de datos", "Mantiene el calendario macro y la fuente de cada cifra. Si no está confirmado, no está en el calendario."),
    ("contable", "Contable", "Lleva el historial completo y el cálculo FIFO para Hacienda. Le gustan las hojas cuadradas."),
    ("cronista", "Cronista de mercados", "Convierte el parte del día en un texto corto con datos. Llama simulación a la simulación."),
]
# What each person really does (shown in the office), and the automatic job they own (a switch in the panel)
FUNCION = {
    "vigia": ("Descarga las velas de BTC y ETH de Kraken cada minuto, guarda el histórico y lanza las alertas. Datos públicos, sin tokens.", "velas"),
    "cuant": ("Prueba las estrategias contra el histórico con walk-forward y las juzga solo fuera de muestra (página Backtest). Sin dinero: solo números.", None),
    "operador": ("Llevará el bot en simulación (Fase 2). De momento solo apunta en el diario lo que tú le digas.", None),
    "riesgo": ("Vigila que nada se salte las reglas de riesgo. Sin dinero real no tiene nada que parar todavía.", None),
    "datos": ("Mantiene el calendario macro y el histórico de velas de Kraken (python app.py historico: CSV trimestral + API). Si no está confirmado, no está en el calendario.", None),
    "contable": ("Llevará el historial y el FIFO cuando haya operaciones. Todavía nada que contar.", None),
    "cronista": ("Redacta el parte diario y lo manda por Telegram a la hora de config.yaml.", "parte"),
}
AUTOMATICOS = {
    "velas": "Vigilar el mercado (velas cada minuto)",
    "alertas": "Avisar por Telegram (roturas del día y picos de volumen)",
    "parte": "Parte diario por Telegram (a la hora de config.yaml)",
    "analisis": "Análisis de cada día con IA (a la hora de config.yaml)",
    "radar": "Radar de noticias con IA (una vez al día, a la hora de config.yaml)",
    "papel": "Cartera de mentira de 500 € (una vez al día, simulación)",
}
REGLAS_RIESGO = [
    "1 % de riesgo por operación",
    "Siempre con stop",
    "−3 % en el día: se para",
    "−12 % acumulado: se apaga todo",
    "Máximo 30 % en un mismo activo",
]
NOMBRES = ["Sofía", "Marcos", "Lucía", "Héctor", "Valentina", "Tomás", "Rafael", "Alejandra", "Javier", "Irene",
           "Mateo", "Carla", "Diego", "Noa", "Pablo", "Elena", "Hugo", "Marta", "Álvaro", "Julia", "Adrián",
           "Paula", "Daniel", "Nerea", "Iker", "Aitana", "Bruno", "Olivia"]
PIELES = ["#f1c9a5", "#e0ac84", "#c68a5e", "#8d5a3b", "#f5d6bd", "#a86f47"]
PELOS = ["#2b1d14", "#5a3a1e", "#c9a45c", "#1a1a1a", "#8b3a1f", "#d9d2c5", "#3b2a4a"]
ROPAS = ["#3b6ea8", "#b0413e", "#3f8f5a", "#7a4fa3", "#d08a2e", "#2f7f86", "#555c66", "#a33b76"]
SALA_ID = "mercados"


def _h(texto):
    return int(hashlib.md5(texto.encode()).hexdigest(), 16)


def _ruta():
    return nucleo.DATOS_DIR / "equipo.yaml"


def _escribir(miembros):
    _ruta().parent.mkdir(parents=True, exist_ok=True)
    _ruta().write_text("# Equipo de la sala. Puedes cambiar nombres, personalidades y colores.\n"
                       + yaml.safe_dump(miembros, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _generar():
    base = _h(SALA_ID)
    miembros = []
    for k, (rol, nombre_rol, persona) in enumerate(ROLES):
        semilla = base + k * 7919
        miembros.append({
            "id": f"{SALA_ID}-{rol}", "rol": rol, "rol_nombre": nombre_rol,
            "nombre": NOMBRES[(base + k * 5) % len(NOMBRES)],
            "persona": persona,
            "piel": PIELES[semilla % len(PIELES)], "pelo": PELOS[(semilla // 3) % len(PELOS)],
            "ropa": ROPAS[(semilla // 11) % len(ROPAS)],
        })
    vistos = set()   # no repeated names inside the team
    for m in miembros:
        while m["nombre"] in vistos:
            m["nombre"] = NOMBRES[(NOMBRES.index(m["nombre"]) + 1) % len(NOMBRES)]
        vistos.add(m["nombre"])
    return miembros


def equipo():
    """The team, created once and saved to datos/equipo.yaml; a role added later is filled in keeping the others."""
    ruta = _ruta()
    miembros = None
    if ruta.is_file():
        try:
            with open(ruta, encoding="utf-8") as fh:
                miembros = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            miembros = None
    if not isinstance(miembros, list) or not all(isinstance(m, dict) and m.get("rol") for m in miembros):
        miembros = None
    if miembros is not None:
        faltan = [r for r in ROLES if r[0] not in {m["rol"] for m in miembros}]
        if not faltan:
            return [_con_funcion(m) for m in miembros]
        por_rol = {m["rol"]: m for m in miembros}
        miembros = [por_rol.get(m["rol"], m) for m in _generar()]
    else:
        miembros = _generar()
    _escribir(miembros)
    return [_con_funcion(m) for m in miembros]


def _con_funcion(m):
    funcion, tarea = FUNCION.get(m.get("rol"), ("", None))
    return {**m, "funcion": funcion, "automatico": tarea}
