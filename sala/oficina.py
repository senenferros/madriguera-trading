"""The visual office: one department per area of the room, each with its screens fed by data the room already keeps
(the bolsa cache traffic lights, the latest daily AI analysis, the automatic switches and the backtest banner).

Pure on purpose: `estado()` takes everything it needs as arguments (the panel passes the real things, the tests pass
fakes), so building the office never touches the network. Everything about bots or money is simulación.
"""
import hashlib

SIM = "Simulación · sin dinero real"

# (id, painted name, colour, market types shown on its screens)
DEPARTAMENTOS = [
    ("bolsa", "Bolsa", "#4cc38a", ("indice",)),
    ("empresas", "Empresas", "#6aa0ff", ("accion",)),
    ("materias", "Materias primas", "#e0b04a", ("metal", "materia_prima")),
    ("cripto", "Criptomonedas", "#f29a4a", ("cripto",)),
    ("analisis", "Análisis IA", "#b38cff", ()),
    ("radar", "Radar de noticias", "#4fd1d9", ()),
    ("riesgo", "Riesgo", "#ff6b7a", ()),
    ("bots", "Sala de bots (simulación)", "#f4a3b5", ()),
]
# which team member sits in which department (the rest get a helper with a deterministic name)
ROL_DEP = {"vigia": "cripto", "cuant": "bots", "operador": "bots", "riesgo": "riesgo", "datos": "radar",
           "contable": "empresas", "cronista": "analisis"}
AYUDANTES = ["Nuria", "Gonzalo", "Ainhoa", "Rubén", "Clara", "Sergio", "Lola", "Íñigo", "Vera", "Óscar", "Maite", "Raúl"]
PIELES = ["#f1c9a5", "#e0ac84", "#c68a5e", "#8d5a3b", "#f5d6bd", "#a86f47"]
PELOS = ["#2b1d14", "#5a3a1e", "#c9a45c", "#1a1a1a", "#8b3a1f", "#d9d2c5", "#3b2a4a"]
TAREAS = {
    "bolsa": ["Mirando el S&P 500", "Comparando índices", "Revisando la media de 200 días"],
    "empresas": ["Leyendo cierres de empresas", "Mirando Nvidia y Apple", "Anotando máximos del año"],
    "materias": ["Mirando el oro", "Siguiendo el Brent", "Comparando metales"],
    "cripto": ["Velas de Kraken", "Mirando BTC y ETH", "Midiendo lo que se mueve"],
    "analisis": ["Leyendo el análisis de hoy", "Comprobando que no invente números", "Quitando consejos"],
    "radar": ["Buscando la fuente", "Comprobando fechas", "Archivando noticias"],
    "riesgo": ["Repasando las reglas", "Diciendo que no", "Contando semáforos rojos"],
    "bots": ["Backtest en simulación", "Juzgando fuera de muestra", "Diario simulado"],
}


def _h(texto):
    return int(hashlib.md5(texto.encode("utf-8")).hexdigest(), 16)


def _coma(x, d=1, signo=True):
    if x is None:
        return "—"
    s = f"{x:+,.{d}f}" if signo else f"{x:,.{d}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _pantalla_mercado(t):
    n = t.get("numeros") or {}
    precio = (t.get("datos") or [""])[0].replace("Precio: ", "") if t.get("datos") else "Sin datos"
    return {"titulo": t.get("nombre", "?"), "color": t.get("color", "gris"), "valor": precio.split(" (")[0],
            "linea": (_coma(n.get("hoy_pct")) + " % hoy") if n else (t.get("nota") or "Sin datos todavía"),
            "texto": t.get("titular", "")}


def _resumen_luces(tarjetas):
    cuenta = {c: 0 for c in ("verde", "amarillo", "rojo", "gris")}
    for t in tarjetas:
        cuenta[t.get("color", "gris") if t.get("color") in cuenta else "gris"] += 1
    return cuenta


def _trabajadores(dep_id, equipo):
    gente = [m for m in equipo if ROL_DEP.get(m.get("rol")) == dep_id]
    k = 0
    while len(gente) < 3:
        s = _h(f"{dep_id}-{k}")
        nombre = AYUDANTES[s % len(AYUDANTES)]
        while nombre in {g["nombre"] for g in gente}:
            nombre = AYUDANTES[(AYUDANTES.index(nombre) + 1) % len(AYUDANTES)]
        gente.append({"nombre": nombre, "rol_nombre": "Ayudante", "piel": PIELES[s % len(PIELES)],
                      "pelo": PELOS[(s // 7) % len(PELOS)], "ropa": None, "id": f"{dep_id}-ayudante-{k}"})
        k += 1
    tareas = TAREAS.get(dep_id, ["Trabajando"])
    return [{"id": m.get("id") or m["nombre"], "nombre": m["nombre"], "rol": m.get("rol_nombre", ""), "piel": m.get("piel", "#e0ac84"),
             "pelo": m.get("pelo", "#2b1d14"), "ropa": m.get("ropa"), "tarea": tareas[i % len(tareas)]}
            for i, m in enumerate(gente[:4])]


def estado(tarjetas=None, analisis=None, automatico=None, banner=None, equipo=None, reglas=None, trabajos=None, hora="", papel=None):
    """Everything the office page draws, as plain JSON. All arguments are optional (missing data shows as such)."""
    tarjetas, equipo, reglas, automatico, trabajos = tarjetas or [], equipo or [], reglas or [], automatico or {}, trabajos or {}
    analisis = analisis if isinstance(analisis, dict) else None
    luces = _resumen_luces(tarjetas)
    deps = []
    for dep_id, nombre, color, tipos in DEPARTAMENTOS:
        pantallas = []
        if tipos:
            pantallas = [_pantalla_mercado(t) for t in tarjetas if t.get("tipo") in tipos]
            if not pantallas:
                pantallas = [{"titulo": nombre, "color": "gris", "valor": "Sin datos", "linea": "Pulsa «Actualizar precios» en la portada", "texto": ""}]
        elif dep_id == "analisis":
            if analisis:
                riesgos = [((m.get("riesgo") or {}).get("nivel")) for m in (analisis.get("mercados") or {}).values()]
                pantallas = [{"titulo": f"Análisis del {analisis.get('fecha', '?')}", "color": "morado",
                              "valor": analisis.get("titular") or "Análisis del día", "linea": f"Hecho a las {analisis.get('hora', '?')}",
                              "texto": analisis.get("resumen_general", "")},
                             {"titulo": "Riesgo según la IA", "color": "amarillo" if riesgos.count("alto") else "verde",
                              "valor": f"{riesgos.count('alto')} alto · {riesgos.count('medio')} medio · {riesgos.count('bajo')} bajo",
                              "linea": "Solo describe: no es un consejo", "texto": analisis.get("aviso", "")}]
            else:
                pantallas = [{"titulo": "Análisis IA", "color": "gris", "valor": "Todavía no hay análisis",
                              "linea": "Se hace una vez al día", "texto": ""}]
        elif dep_id == "radar":
            nombres = {t.get("clave"): t.get("nombre") for t in tarjetas}
            for clave, m in ((analisis or {}).get("mercados") or {}).items():
                n = m.get("noticia") if isinstance(m, dict) else None
                if isinstance(n, dict) and n.get("texto"):
                    pantallas.append({"titulo": nombres.get(clave, clave), "color": "cian", "valor": n["texto"][:140],
                                      "linea": " · ".join(x for x in (n.get("fuente", "")[:60], n.get("fecha", "")) if x), "texto": ""})
            pantallas = pantallas[:6] or [{"titulo": "Radar de noticias", "color": "gris", "valor": "Sin noticias guardadas",
                                           "linea": "Llegan con el análisis del día", "texto": ""}]
        elif dep_id == "riesgo":
            pantallas = [{"titulo": "Semáforos", "color": "rojo" if luces["rojo"] else "verde",
                          "valor": f"{luces['verde']} verde · {luces['amarillo']} amarillo · {luces['rojo']} rojo",
                          "linea": f"{luces['gris']} sin datos", "texto": "El color describe el mercado, no es una orden."},
                         {"titulo": "Reglas (simulación)", "color": "amarillo", "valor": reglas[0] if reglas else "—",
                          "linea": " · ".join(reglas[1:3]), "texto": " · ".join(reglas[3:])}]
        elif dep_id == "bots":
            b = banner or {}
            pantallas = [{"titulo": "Bots · simulación", "color": "amarillo" if b.get("hay") else "gris",
                          "valor": b.get("titulo") or "Sin backtests todavía", "linea": SIM, "texto": b.get("texto", "")}]
            pantallas.extend(_pantallas_papel(papel))
            for clave, a in automatico.items():
                pantallas.append({"titulo": a.get("nombre", clave), "color": "verde" if a.get("activo") else "gris",
                                  "valor": "Encendido" if a.get("activo") else "Apagado", "linea": "Simulación", "texto": ""})
            for clave in ("backtest", "vigia"):
                tr = trabajos.get(clave)
                if isinstance(tr, dict):
                    pantallas.append({"titulo": f"Trabajo {clave} (simulación)", "color": "verde" if tr.get("estado") == "corriendo" else "gris",
                                      "valor": str(tr.get("estado") or "parado").capitalize(), "linea": (tr.get("log") or [""])[-1][-80:], "texto": ""})
        deps.append({"id": dep_id, "nombre": nombre, "color": color, "pantallas": pantallas[:6],
                     "trabajadores": _trabajadores(dep_id, equipo)})
    return {"cartel": "LA MADRIGUERA TRADING", "simulacion": SIM, "hora": hora, "luces": luces, "departamentos": deps,
            "papel": papel if isinstance(papel, dict) else None}


def _pantallas_papel(p):
    """The paper portfolio's screens in the bots room: balance and days, open positions, last closed trade."""
    if not isinstance(p, dict) or not p.get("empezada"):
        return [{"titulo": "Cartera de mentira (500 €) · simulación", "color": "gris", "valor": "Todavía no ha empezado",
                 "linea": "Empieza en la primera evaluación del día", "texto": "Simulación: no hay dinero real."}]
    color = "rojo" if p.get("apagado") else ("verde" if p.get("resultado", 0) >= 0 else "amarillo")
    out = [{"titulo": "Cartera de mentira (500 €) · simulación", "color": color,
            "valor": _coma(p.get("saldo"), 2, signo=False) + " €",
            "linea": f"{_coma(p.get('resultado'), 2)} € · día {p.get('dias', 0)} de {p.get('dias_plan', 91)}",
            "texto": ("Apagada por el −12 %. " if p.get("apagado") else "") + "Simulación: no hay dinero real."}]
    pos = p.get("posiciones") or []
    out.append({"titulo": "Posiciones abiertas (simulación)", "color": "verde" if pos else "gris",
                "valor": ", ".join(x["nombre"] for x in pos) or "Ninguna",
                "linea": " · ".join(f"{x['nombre']} {_coma(x['resultado'], 2)} €" for x in pos)[:120], "texto": ""})
    ops = p.get("operaciones") or []
    if ops:
        o = ops[0]
        out.append({"titulo": "Última operación (simulación)", "color": "verde" if o["pnl"] > 0 else "rojo",
                    "valor": f"{o['nombre']} {_coma(o['pnl'], 2)} €", "linea": f"{o['fecha_salida']} · {o.get('motivo_texto', o['motivo'])}",
                    "texto": f"{p.get('ganadas', 0)} ganadas · {p.get('perdidas', 0)} perdidas"})
    return out
