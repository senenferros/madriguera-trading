"""Fase 2: la cartera de mentira. 500 € ficticios que siguen, día a día, las cuatro estrategias que dieron PASA en
los backtests de bolsa (S&P 500, Nasdaq 100 y DAX con rebote_minimo; Apple con donchian_dia).

Same risk rules as the backtest (sala/backtest.py): 1 % of the portfolio at risk per trade sized by `backtest.tamano`,
always a stop, 30 % cap per asset, -3 % day stop, -12 % kill switch, per-market costs, signal at the close and
execution at the next open. The four markets share ONE 500 € account. Prices are the daily Yahoo candles of the
history store (`python app.py historico yahoo`); no currency conversion, as in the backtest (index points = euros).

Incremental and idempotent: each evaluation only processes the daily bars newer than the last one seen per market,
so running it twice a day changes nothing. The state lives in datos/papel/estado.json. Nothing here moves money:
it is a simulation from start to end.
"""
from datetime import date, datetime, timedelta, timezone

import nucleo
from sala import backtest, estrategias, historico, mercado

CAPITAL = 500.0
DIAS_PLAN = 91                # the planned three months
LOOKBACK_DIAS = 400           # calendar days of history loaded before the start (indicators and pivots)
CARTERA = [
    {"par": "SPX500", "estrategia": "rebote_minimo"},
    {"par": "NDX100", "estrategia": "rebote_minimo"},
    {"par": "DAX40EUR", "estrategia": "rebote_minimo"},
    {"par": "AAPLUSD", "estrategia": "donchian_dia"},
]
AVISO = "Simulación: 500 € de mentira, sin dinero ni órdenes reales. Que vaya bien o mal tres meses no demuestra nada."
MOTIVOS = {"stop": "saltó el stop", "senal": "señal de salida", "tiempo": "40 sesiones sin salir", "parada_dia": "parada del día (−3 %)",
           "apagado": "apagado de la cartera (−12 %)"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ruta():
    return nucleo.DATOS_DIR / "papel" / "estado.json"


def _fecha(t):
    return (_EPOCH + timedelta(seconds=int(t))).date().isoformat()


def cargar():
    """The saved state, or None before the first evaluation."""
    st = mercado._leer_json(_ruta(), None)
    return st if isinstance(st, dict) and st.get("version") == 1 else None


def guardar(st):
    mercado._escribir_json(_ruta(), st)


def _configuracion(cfg=None):
    c = dict(backtest.configuracion(cfg))
    c["capital_inicial"] = CAPITAL
    return c


def _nuevo(hoy):
    return {"version": 1, "inicio": hoy, "capital_inicial": CAPITAL, "efectivo": CAPITAL, "pico": CAPITAL,
            "apagado": False, "parado_dia": None, "dia": None, "inicio_dia": CAPITAL, "equity_prev": CAPITAL,
            "mercados": {}, "operaciones": [], "curva": [], "ultima_evaluacion": None, "avisos": []}


def _equity(st):
    return st["efectivo"] + sum(m["posicion"]["cantidad"] * m["cierre"] for m in st["mercados"].values() if m.get("posicion"))


def evaluar(hoy=None, avisar=print, cfg=None):
    """Process the new daily bars of the four markets and save the state. Returns resumen()."""
    hoy = hoy or date.today().isoformat()
    c = _configuracion(cfg)
    st = cargar() or _nuevo(hoy)
    st["avisos"] = []
    velas, idx, ctx = {}, {}, {}
    for item in CARTERA:
        par, est = item["par"], estrategias.REGISTRO[item["estrategia"]]
        nombre = backtest.nombre_mercado(par)
        rango = historico.rango_disponible(par, 1440)
        if not rango:
            st["avisos"].append(f"{nombre}: sin histórico diario (ejecuta python app.py historico yahoo)")
            continue
        m = st["mercados"].get(par)
        if m is None:
            # first sight of this market: fix the start of the loaded history so bar indices stay stable
            t0 = (int(rango[1]) - LOOKBACK_DIAS * 86400) // 86400 * 86400
            m = {"t0": t0, "ultimo_t": None, "cierre": None, "posicion": None, "pendiente": None, "memoria": {},
                 "estrategia": item["estrategia"], "nombre": nombre}
        v = historico.cargar(par, m["t0"], int(rango[1]), 1440)
        if len(v) < est.calentamiento + 2:
            st["avisos"].append(f"{nombre}: pocos días de datos ({len(v)})")
            continue
        if m["ultimo_t"] is None:
            # start trading from now on: the last closed bar only gives a signal for the next open
            m["ultimo_t"] = v[-2][0]
            m["cierre"] = v[-2][4]
        st["mercados"][par] = m
        velas[par] = v
        idx[par] = {b[0]: i for i, b in enumerate(v)}
        ctx[par] = est.preparar(v, est.defecto, c["dia"])
    nuevos = sorted({b[0] for par, v in velas.items() for b in v if b[0] > st["mercados"][par]["ultimo_t"]})
    for t in nuevos:
        _dia(st, t, velas, idx, ctx, c)
    st["ultima_evaluacion"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    guardar(st)
    if nuevos:
        avisar(f"Cartera de mentira: {len(nuevos)} días nuevos hasta el {_fecha(nuevos[-1])}")
    else:
        avisar("Cartera de mentira: no hay días nuevos")
    for a in st["avisos"]:
        avisar(a)
    return resumen(st, hoy)


def _dia(st, t, velas, idx, ctx, c):
    dia = _fecha(t)
    hoy_pares = [p for p in velas if t in idx[p] and t > st["mercados"][p]["ultimo_t"]]
    # 1-2. at the open: yesterday's orders; inside the bar: the stops (same order and fills as backtest.simular)
    for par in hoy_pares:
        m, i = st["mercados"][par], idx[par][t]
        k = backtest.costes_par(c, par)
        com, desl, desl_stop = k["comision_pct"] / 100, k["deslizamiento_pct"] / 100, k["deslizamiento_stop_pct"] / 100
        _, o, h, l, cierre = velas[par][i][:5]
        pend = m.get("pendiente")
        if pend:
            if pend["accion"] == "comprar":
                if m["posicion"] is None and not st["apagado"] and st["parado_dia"] != dia:
                    q, riesgo, entrada_ef, motivo = backtest.tamano(pend["capital"], st["efectivo"], o, pend.get("stop"), k)
                    if motivo is None:
                        coste = q * entrada_ef * (1 + com)
                        st["efectivo"] -= coste
                        m["posicion"] = {"entrada": entrada_ef, "stop": float(pend["stop"]), "stop_inicial": float(pend["stop"]),
                                         "cantidad": q, "entrada_t": t, "riesgo": riesgo, "coste_entrada": coste, "open_entrada": o}
                    else:
                        st["avisos"].append(f"{m['nombre']}: compra descartada el {dia} ({motivo})")
            elif m["posicion"] is not None:
                _cerrar(st, par, o * (1 - desl), pend["motivo"], t, com)
            m["pendiente"] = None
        pos = m["posicion"]
        if pos is not None:
            if o <= pos["stop"]:
                _cerrar(st, par, o * (1 - desl_stop), "stop", t, com)
            elif l <= pos["stop"]:
                _cerrar(st, par, pos["stop"] * (1 - desl_stop), "stop", t, com)
        m["cierre"] = cierre
    # 3. at the close: equity, day stop, kill switch, then the strategies
    equity = _equity(st)
    st["curva"].append([t, round(equity, 2)])
    st["pico"] = max(st["pico"], equity)
    if dia != st["dia"]:
        st["dia"] = dia
        st["inicio_dia"] = st["equity_prev"]
    abiertas = [p for p, m in st["mercados"].items() if m.get("posicion")]
    if st["parado_dia"] != dia and st["inicio_dia"] > 0 and equity <= st["inicio_dia"] * (1 - c["parada_dia_pct"] / 100):
        st["parado_dia"] = dia
        for p in abiertas:
            st["mercados"][p]["pendiente"] = {"accion": "vender", "motivo": "parada_dia"}
    if not st["apagado"] and st["pico"] > 0 and (st["pico"] - equity) / st["pico"] >= c["apagado_pct"] / 100:
        st["apagado"] = True
        for p in abiertas:
            st["mercados"][p]["pendiente"] = {"accion": "vender", "motivo": "apagado"}
    for par in hoy_pares:
        m, i = st["mercados"][par], idx[par][t]
        est = estrategias.REGISTRO[m["estrategia"]]
        if m["pendiente"] is None and not st["apagado"]:
            pos = m["posicion"]
            vista = None if pos is None else {**pos, "entrada_i": idx[par].get(pos["entrada_t"], i)}
            s = est.senal(i, velas[par], ctx[par], vista, est.defecto, m["memoria"])
            if s:
                if s.get("accion") == "comprar" and pos is None and st["parado_dia"] != dia:
                    m["pendiente"] = {"accion": "comprar", "stop": s.get("stop"), "capital": equity}
                elif s.get("accion") == "vender" and pos is not None:
                    m["pendiente"] = {"accion": "vender", "motivo": s.get("motivo") if s.get("motivo") in ("senal", "tiempo") else "senal"}
                elif pos is not None and s.get("stop") is not None and float(s["stop"]) > pos["stop"]:
                    pos["stop"] = float(s["stop"])
        m["ultimo_t"] = t
    st["equity_prev"] = equity


def _cerrar(st, par, salida, motivo, t, com):
    m = st["mercados"][par]
    pos = m["posicion"]
    ingreso = pos["cantidad"] * salida * (1 - com)
    st["efectivo"] += ingreso
    pnl = ingreso - pos["coste_entrada"]
    st["operaciones"].append({
        "par": par, "nombre": m["nombre"], "estrategia": m["estrategia"], "fecha_entrada": _fecha(pos["entrada_t"]),
        "fecha_salida": _fecha(t), "entrada": round(pos["entrada"], 4), "salida": round(salida, 4),
        "stop": round(pos["stop_inicial"], 4), "cantidad": round(pos["cantidad"], 6), "pnl": round(pnl, 2),
        "R": round(pnl / pos["riesgo"], 2) if pos["riesgo"] else 0.0, "motivo": motivo})
    m["posicion"] = None


def resumen(st=None, hoy=None):
    """Plain data for the pages and the CLI. {"empezada": False} before the first evaluation."""
    st = st if st is not None else cargar()
    hoy = hoy or date.today().isoformat()
    if not st:
        return {"empezada": False, "inicial": CAPITAL, "dias_plan": DIAS_PLAN, "aviso": AVISO}
    saldo = _equity(st)
    posiciones = []
    for par, m in st["mercados"].items():
        p = m.get("posicion")
        if p:
            valor = p["cantidad"] * m["cierre"]
            posiciones.append({"nombre": m["nombre"], "estrategia": estrategias.REGISTRO[m["estrategia"]].titulo,
                               "desde": _fecha(p["entrada_t"]), "entrada": round(p["entrada"], 2), "stop": round(p["stop"], 2),
                               "precio": round(m["cierre"], 2), "valor": round(valor, 2),
                               "resultado": round(valor - p["coste_entrada"], 2)})
    ops = [dict(o, motivo_texto=MOTIVOS.get(o["motivo"], o["motivo"])) for o in reversed(st["operaciones"])]
    try:
        dias = max(0, (date.fromisoformat(hoy) - date.fromisoformat(st["inicio"])).days)
    except ValueError:
        dias = 0
    return {"empezada": True, "inicial": st["capital_inicial"], "saldo": round(saldo, 2), "efectivo": round(st["efectivo"], 2),
            "resultado": round(saldo - st["capital_inicial"], 2), "resultado_pct": round((saldo / st["capital_inicial"] - 1) * 100, 2),
            "posiciones": posiciones, "operaciones": ops, "ganadas": sum(1 for o in ops if o["pnl"] > 0),
            "perdidas": sum(1 for o in ops if o["pnl"] <= 0), "inicio": st["inicio"], "dias": dias, "dias_plan": DIAS_PLAN,
            "apagado": st["apagado"], "ultima_evaluacion": st.get("ultima_evaluacion"), "avisos": st.get("avisos", []),
            "mercados": [backtest.nombre_mercado(x["par"]) + " · " + estrategias.REGISTRO[x["estrategia"]].titulo for x in CARTERA],
            "aviso": AVISO}


def _e(x):
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def texto_cli(r):
    if not r.get("empezada"):
        return ["Cartera de mentira (500 €, simulación): todavía no ha empezado.", r["aviso"]]
    out = [f"Cartera de mentira ({_e(r['inicial'])}, simulación) · día {r['dias']} de {r['dias_plan']}",
           f"Saldo: {_e(r['saldo'])} ({r['resultado']:+.2f} €, {r['resultado_pct']:+.2f} %) · efectivo {_e(r['efectivo'])}"]
    if r["apagado"]:
        out.append("APAGADA: ha perdido un 12 % desde su máximo. No abre nada más hasta que la revises.")
    out.append("Posiciones abiertas:" if r["posiciones"] else "Sin posiciones abiertas.")
    for p in r["posiciones"]:
        out.append(f"  {p['nombre']} desde el {p['desde']}: entrada {p['entrada']}, stop {p['stop']}, ahora {p['precio']} ({p['resultado']:+.2f} €)")
    out.append(f"Operaciones cerradas: {len(r['operaciones'])} ({r['ganadas']} ganadas, {r['perdidas']} perdidas)")
    for o in r["operaciones"][:20]:
        out.append(f"  {o['fecha_salida']} {o['nombre']}: {o['pnl']:+.2f} € ({o['R']:+.2f} R, {o['motivo_texto']})")
    out.extend(r["avisos"])
    out.append(r["aviso"])
    return out


def actualizar_datos(avisar=print, cfg=None):
    """Download the daily Yahoo history of the four markets (network)."""
    from sala import yahoo
    return yahoo.actualizar([x["par"] for x in CARTERA], avisar=avisar, cfg=cfg)
