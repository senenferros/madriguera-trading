"""El backtester del analista cuantitativo: motor de barras con las cinco reglas de riesgo (`equipo.REGLAS_RIESGO`),
costes, métricas, walk-forward, referencias (comprar y mantener, entradas al azar), puertas, veredicto, avisos y los
ficheros de datos/backtests/.

Determinista: `random.Random(semilla)` explícito, sin `time.time()` dentro del motor (solo `correr` para `id`, `fecha`
y `ahora`). Long-only, al contado, un par por backtest. Lo que pasó no predice lo que pasará.

Simulación · datos públicos · sin dinero real.
"""
import calendar
import math
import random
import re
import statistics
import time
from bisect import bisect_left
from datetime import date, datetime, timezone

import nucleo
from sala import estrategias, mercado
from sala.estrategias import Estrategia, dia_de

AVISO_HONESTO = ("Esto es una simulación sobre precios pasados de Kraken, con comisiones y deslizamiento estimados; ninguna orden "
                 "se ha enviado a ningún sitio. Lo que pasó no predice lo que pasará: una estrategia que aprueba fuera de muestra "
                 "puede dejar de funcionar mañana, y la mayoría de quien hace trading a corto plazo pierde dinero. Cada backtest "
                 "es una prueba más: de 20 estrategias sin ventaja real, una aprobaría las puertas por puro azar. «PASA» solo "
                 "significa que la estrategia merece meses de paper trading (Fase 2), nunca dinero real.")
VEREDICTOS = {"pasa": "PASA las puertas de Fase 1 fuera de muestra", "no_pasa": "NO PASA las puertas de Fase 1", "insuficiente": "INSUFICIENTE"}
MOTIVOS_SALIDA = ("stop", "objetivo", "senal", "tiempo", "parada_dia", "apagado", "fin")
RE_ID = re.compile(r"^\d{8}-\d{6}-[a-z_]+-[A-Z0-9]{6,12}$")
MAX_INDICE = 200
PUNTOS_CURVA = 600
BARRAS_MINIMAS = 100

DEFECTOS = {
    "capital_inicial": 10000, "comision_pct": 0.40, "deslizamiento_pct": 0.05, "deslizamiento_stop_pct": 0.10,
    "minimo_orden_eur": 10, "riesgo_pct": 1.0, "tope_activo_pct": 30, "parada_dia_pct": 3, "apagado_pct": 12,
    "stop_min_pct": 0.3, "dia": "local",
    "ventana_is_dias": 180, "ventana_oos_dias": 60, "oos_min_dias": 30, "ventanas_minimas": 4,
    "min_operaciones_is": 30, "hueco_max_dias": 7, "semillas_azar": 200, "semillas_azar_panel": 50,
    "max_operaciones_guardadas": 5000,
}
PUERTAS_DEFECTO = {"expectativa_min": 0, "profit_factor_min": 1.3, "drawdown_max_pct": 20, "operaciones_min": 100,
                   "p_azar_max": 0.05, "consistencia_min": 0.5}
_ENTEROS = ("ventana_is_dias", "ventana_oos_dias", "oos_min_dias", "ventanas_minimas", "min_operaciones_is",
            "hueco_max_dias", "semillas_azar", "semillas_azar_panel", "max_operaciones_guardadas")


# ---------- config (§6.1) ----------

def _numero(valor, defecto, entero=False, minimo=None):
    try:
        if isinstance(valor, bool) or valor is None:
            raise TypeError
        x = int(valor) if entero else float(valor)
        if not math.isfinite(x):
            raise ValueError
    except (TypeError, ValueError):
        return defecto
    if minimo is not None and x < minimo:
        return defecto
    return x


def configuracion(cfg=None):
    """La sección `backtest` de config.yaml validada con sus defaults: funciona aunque la sección falte del todo."""
    cfg = cfg if cfg is not None else nucleo.cargar_config()
    bt = cfg.get("backtest") if isinstance(cfg, dict) else None
    bt = bt if isinstance(bt, dict) else {}
    salida = {}
    for k, d in DEFECTOS.items():
        if k == "dia":
            salida[k] = "utc" if str(bt.get("dia", d)).strip().lower() == "utc" else "local"
        elif k in _ENTEROS:
            salida[k] = _numero(bt.get(k), d, entero=True, minimo=0)
        else:
            salida[k] = _numero(bt.get(k), d, minimo=0)
    salida["ventana_is_dias"] = max(1, salida["ventana_is_dias"])
    salida["ventana_oos_dias"] = max(1, salida["ventana_oos_dias"])
    salida["ventanas_minimas"] = max(1, salida["ventanas_minimas"])
    puertas = bt.get("puertas") if isinstance(bt.get("puertas"), dict) else {}
    salida["puertas"] = {k: _numero(puertas.get(k), d) for k, d in PUERTAS_DEFECTO.items()}
    return salida


def _cfg(cfg):
    """Accept a validated config or a raw one (or None -> config.yaml)."""
    if isinstance(cfg, dict) and "capital_inicial" in cfg and isinstance(cfg.get("puertas"), dict) and "backtest" not in cfg:
        return cfg
    return configuracion(cfg)


# ---------- helpers: numbers and dates for humans (§2.4) ----------

def _r(x, d):
    return None if x is None else round(float(x), d)


def _coma(x, d=2, signo=False):
    """Number with a decimal comma (and a leading + when asked); '—' for None."""
    if x is None:
        return "—"
    s = f"{x:+.{d}f}" if signo else f"{x:.{d}f}"
    return s.replace(".", ",").replace("-", "−")


def _fecha(t):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(t))


def _fecha_corta(t):
    return time.strftime("%d/%m/%Y", time.localtime(t))


def _fecha_utc(t):
    return datetime.fromtimestamp(t, timezone.utc).date().isoformat()


def _epoch_fecha(texto):
    """'AAAA-MM-DD' -> epoch of that UTC midnight."""
    d = date.fromisoformat(str(texto).strip())
    return calendar.timegm((d.year, d.month, d.day, 0, 0, 0))


def _carpeta():
    return nucleo.DATOS_DIR / "backtests"


# ---------- sizing: the 1 % rule, the 30 % cap, no leverage (§6.5) ----------

def tamano(capital, efectivo, open_, stop, cfg):
    """(cantidad, riesgo_planeado, entrada_ef, motivo_rechazo|None): a stop exit costs exactly -1R, fees included."""
    com = cfg["comision_pct"] / 100
    desl = cfg["deslizamiento_pct"] / 100
    desl_stop = cfg["deslizamiento_stop_pct"] / 100
    entrada_ef = open_ * (1 + desl)
    if stop is None or not isinstance(stop, (int, float)) or isinstance(stop, bool) or not math.isfinite(stop) or stop <= 0:
        return 0.0, 0.0, entrada_ef, "sin_stop"
    if stop >= entrada_ef * (1 - cfg["stop_min_pct"] / 100):
        return 0.0, 0.0, entrada_ef, "stop_alto"
    stop_ef = stop * (1 - desl_stop)
    perdida_unidad = (entrada_ef - stop_ef) + com * (entrada_ef + stop_ef)
    riesgo = capital * cfg["riesgo_pct"] / 100
    cantidad = riesgo / perdida_unidad
    cantidad = min(cantidad, cfg["tope_activo_pct"] / 100 * capital / entrada_ef)
    cantidad = min(cantidad, efectivo / (entrada_ef * (1 + com)))
    if cantidad <= 0 or cantidad * entrada_ef < cfg["minimo_orden_eur"]:
        return 0.0, 0.0, entrada_ef, "minimo"
    return cantidad, cantidad * perdida_unidad, entrada_ef, None


# ---------- the bar loop (§6.4) ----------

def _estado_inicial(estado, capital):
    if estado:
        return {"pico": float(estado.get("pico", capital)), "apagado": bool(estado.get("apagado", False)),
                "parado_dia": estado.get("parado_dia"), "inicio_dia": float(estado.get("inicio_dia", capital)),
                "dia": estado.get("dia")}
    return {"pico": float(capital), "apagado": False, "parado_dia": None, "inicio_dia": float(capital), "dia": None}


def simular(velas, estrategia, params, cfg, desde_t=None, hasta_t=None, capital=None, ventana=0, estado=None):
    """Recorre las barras (calentamiento incluido en `velas`): señal al cierre, ejecución al open siguiente, stop y
    objetivo dentro de la barra, parada del día, apagado acumulado (`estado` viene y vuelve) y cierre forzoso al final."""
    com = cfg["comision_pct"] / 100
    desl = cfg["deslizamiento_pct"] / 100
    desl_stop = cfg["deslizamiento_stop_pct"] / 100
    modo = cfg["dia"]
    parada_frac = cfg["parada_dia_pct"] / 100
    apagado_frac = cfg["apagado_pct"] / 100
    capital = float(capital if capital is not None else cfg["capital_inicial"])
    efectivo = capital
    pos = None
    pendiente = None
    memoria = {}
    ctx = estrategia.preparar(velas, params, modo)
    st = _estado_inicial(estado, capital)
    pico, apagado, parado_dia, inicio_dia, dia = st["pico"], st["apagado"], st["parado_dia"], st["inicio_dia"], st["dia"]
    operaciones = []
    curva = []
    rechazadas = 0
    rechazos = {}
    paradas_dia = 0
    apagones = 0
    barras = 0
    barras_en_posicion = 0
    comisiones_total = 0.0
    deslizamiento_total = 0.0
    equity_prev = capital
    ultimo_i = None

    def cerrar(salida, motivo, i, t, teorico):
        nonlocal pos, efectivo, comisiones_total, deslizamiento_total
        q = pos["cantidad"]
        ingreso = q * salida * (1 - com)
        efectivo += ingreso
        pnl = ingreso - pos["coste_entrada"]
        riesgo = pos["riesgo"]
        comis = com * q * (pos["entrada"] + salida)
        deslz = q * (pos["entrada"] - pos["open_entrada"]) + (0.0 if motivo == "objetivo" else q * (teorico - salida))
        comisiones_total += comis
        deslizamiento_total += deslz
        operaciones.append({
            "ventana": ventana, "entrada_t": pos["entrada_t"], "entrada_i": pos["entrada_i"], "salida_t": t, "salida_i": i,
            "entrada": round(pos["entrada"], 4), "salida": round(salida, 4), "stop": round(pos["stop_inicial"], 4),
            "stop_final": round(pos["stop"], 4), "objetivo": _r(pos["objetivo"], 4), "cantidad": round(q, 6),
            "capital_antes": round(pos["capital_antes"], 2), "riesgo": round(riesgo, 2), "pnl": round(pnl, 2),
            "R": round(pnl / riesgo, 3) if riesgo else 0.0, "comisiones": round(comis, 4), "deslizamiento": round(deslz, 4),
            "barras": i - pos["entrada_i"], "motivo": motivo, "fecha_entrada": _fecha(pos["entrada_t"]), "fecha_salida": _fecha(t),
        })
        pos = None

    for i in range(max(0, int(estrategia.calentamiento)), len(velas)):
        v = velas[i]
        t, o, h, l, c = v[0], v[1], v[2], v[3], v[4]
        if hasta_t is not None and t >= hasta_t:
            break
        ultimo_i = i
        dia_i = dia_de(t, modo)
        # 1. at the open: the order decided at the previous close
        if pendiente is not None:
            if pendiente["accion"] == "comprar":
                if pos is None and (desde_t is None or t >= desde_t) and not apagado and parado_dia != dia_i:
                    q, riesgo, entrada_ef, motivo = tamano(pendiente["capital"], efectivo, o, pendiente.get("stop"), cfg)
                    if motivo is None:
                        coste = q * entrada_ef * (1 + com)
                        efectivo -= coste
                        pos = {"entrada": entrada_ef, "stop": float(pendiente["stop"]), "objetivo": pendiente.get("objetivo"),
                               "cantidad": q, "entrada_i": i, "entrada_t": t, "stop_inicial": float(pendiente["stop"]),
                               "capital_antes": pendiente["capital"], "riesgo": riesgo, "coste_entrada": coste, "open_entrada": o}
                    else:
                        rechazadas += 1
                        rechazos[motivo] = rechazos.get(motivo, 0) + 1
            elif pos is not None:
                cerrar(o * (1 - desl), pendiente["motivo"], i, t, o)
            pendiente = None
        # 2. inside the bar: stop first (pessimistic), then the limit target
        if pos is not None:
            stop = pos["stop"]
            if o <= stop:
                cerrar(o * (1 - desl_stop), "stop", i, t, stop)
            elif l <= stop:
                cerrar(stop * (1 - desl_stop), "stop", i, t, stop)
            else:
                objetivo = pos["objetivo"]
                if objetivo is not None:
                    if o >= objetivo:
                        cerrar(o, "objetivo", i, t, objetivo)
                    elif h >= objetivo:
                        cerrar(objetivo, "objetivo", i, t, objetivo)
        # 3. at the close: equity, curve, day stop, kill switch, then the strategy
        equity = efectivo + (pos["cantidad"] * c if pos is not None else 0.0)
        cuenta = desde_t is None or t >= desde_t
        if cuenta:
            curva.append([t, equity])
            barras += 1
            if pos is not None:
                barras_en_posicion += 1
        if equity > pico:
            pico = equity
        if dia_i != dia:
            dia = dia_i
            inicio_dia = equity_prev
        if parado_dia != dia and inicio_dia > 0 and equity <= inicio_dia * (1 - parada_frac):
            parado_dia = dia
            paradas_dia += 1
            if pos is not None:
                pendiente = {"accion": "vender", "motivo": "parada_dia"}
        if not apagado and pico > 0 and (pico - equity) / pico >= apagado_frac:
            apagado = True
            apagones += 1
            if pos is not None:
                pendiente = {"accion": "vender", "motivo": "apagado"}
        if pendiente is None and not apagado:
            s = estrategia.senal(i, velas, ctx, pos, params, memoria)
            if s:
                accion = s.get("accion")
                if accion == "comprar":
                    if pos is None and parado_dia != dia:
                        pendiente = {"accion": "comprar", "stop": s.get("stop"), "objetivo": s.get("objetivo"), "capital": equity}
                elif accion == "vender":
                    if pos is not None:
                        motivo = s.get("motivo") if s.get("motivo") in ("senal", "tiempo") else "senal"
                        pendiente = {"accion": "vender", "motivo": motivo}
                elif pos is not None and s.get("stop") is not None:
                    try:
                        nuevo = float(s["stop"])
                    except (TypeError, ValueError):
                        nuevo = None
                    if nuevo is not None and math.isfinite(nuevo) and nuevo > pos["stop"]:
                        pos["stop"] = nuevo
        equity_prev = equity

    # 4. end of the slice: a still-open position is closed at the last close, paying the costs
    if pos is not None and ultimo_i is not None:
        vl = velas[ultimo_i]
        cerrar(vl[4] * (1 - desl), "fin", ultimo_i, vl[0], vl[4])
    return {"operaciones": operaciones, "curva": curva, "capital_final": efectivo, "rechazadas": rechazadas,
            "rechazos": rechazos, "paradas_dia": paradas_dia, "apagones": apagones, "barras": barras,
            "barras_en_posicion": barras_en_posicion, "comisiones": comisiones_total, "deslizamiento": deslizamiento_total,
            "estado": {"pico": pico, "apagado": apagado, "parado_dia": parado_dia, "inicio_dia": inicio_dia, "dia": dia}}


# ---------- metrics (§6.7) ----------

def max_drawdown(curva):
    """Biggest fall from a running peak, in %, over every bar close."""
    pico = None
    peor = 0.0
    for _, eq in curva:
        if pico is None or eq > pico:
            pico = eq
        if pico > 0:
            dd = (pico - eq) / pico
            if dd > peor:
                peor = dd
    return peor * 100


def duracion_drawdown(curva):
    """Longest stretch (days, 1 decimal) between a peak and its recovery, or until the end of the curve."""
    if not curva:
        return 0.0
    t_pico, pico = curva[0][0], curva[0][1]
    peor = 0.0
    for t, eq in curva:
        if eq >= pico:
            peor = max(peor, (t - t_pico) / 86400)
            t_pico, pico = t, eq
    peor = max(peor, (curva[-1][0] - t_pico) / 86400)
    return round(peor, 1)


def rendimientos_diarios(curva, modo_dia="utc"):
    """Equity at the last close of each day -> daily returns E_d/E_{d-1} - 1 (days without bars do not exist)."""
    cierres = []
    dia = None
    for t, eq in curva:
        d = dia_de(t, modo_dia)
        if d != dia:
            cierres.append(eq)
            dia = d
        else:
            cierres[-1] = eq
    return [cierres[k] / cierres[k - 1] - 1 for k in range(1, len(cierres)) if cierres[k - 1] > 0]


def sharpe_diario(curva, modo_dia="utc", min_dias=30):
    """mean/stdev of the daily returns times sqrt(365) (crypto trades every day, risk-free 0); None without data."""
    r = rendimientos_diarios(curva, modo_dia)
    if len(r) < max(2, min_dias):
        return None
    sd = statistics.stdev(r)
    if sd == 0:
        return None
    return statistics.mean(r) / sd * math.sqrt(365)


def _percentil(valores, p):
    if not valores:
        return None
    s = sorted(valores)
    k = int(round(p * (len(s) - 1)))
    return s[max(0, min(len(s) - 1, k))]


def bootstrap_expectativa(R, n=1000, semilla=0):
    """(p5, p50, p95) of the mean over `n` resamples with replacement; deterministic."""
    if not R:
        return None, None, None
    rng = random.Random(semilla)
    m = len(R)
    medias = [sum(rng.choices(R, k=m)) / m for _ in range(n)]
    return _percentil(medias, 0.05), _percentil(medias, 0.50), _percentil(medias, 0.95)


def _profit_factor(pnls):
    if not pnls:
        return None
    pos = sum(p for p in pnls if p > 0)
    neg = -sum(p for p in pnls if p < 0)
    return None if neg == 0 else pos / neg


def metricas(operaciones, curva, capital_inicial, cfg):
    """Todas las cifras fuera de muestra; con 0 operaciones nada explota (0 o None)."""
    n = len(operaciones)
    pnls = [op["pnl"] for op in operaciones]
    R = [op["R"] for op in operaciones]
    ganadoras = sum(1 for p in pnls if p > 0)
    perdedoras = sum(1 for p in pnls if p <= 0)
    R_g = [op["R"] for op in operaciones if op["pnl"] > 0]
    R_p = [op["R"] for op in operaciones if op["pnl"] <= 0]
    pf = _profit_factor(pnls)
    eq_final = curva[-1][1] if curva else capital_inicial
    dias = (curva[-1][0] - curva[0][0]) / 86400 if len(curva) > 1 else 0.0
    racha = peor_racha = 0
    for p in pnls:
        racha = racha + 1 if p <= 0 else 0
        peor_racha = max(peor_racha, racha)
    p5, p50, p95 = bootstrap_expectativa(R)
    t_R = None
    if n >= 2:
        sd = statistics.stdev(R)
        if sd > 0:
            t_R = statistics.mean(R) / (sd / math.sqrt(n))
    barras = sum(op.get("barras", 0) for op in operaciones)
    return {
        "n": n, "ganadoras": ganadoras, "perdedoras": perdedoras, "win_rate": round(ganadoras / n, 3) if n else 0.0,
        "expectativa_R": round(statistics.mean(R), 3) if n else 0.0,
        "media_R_ganadoras": round(statistics.mean(R_g), 3) if R_g else None,
        "media_R_perdedoras": round(statistics.mean(R_p), 3) if R_p else None,
        "mejor_R": round(max(R), 3) if n else None, "peor_R": round(min(R), 3) if n else None,
        "expectativa_pct": round(statistics.mean([op["pnl"] / op.get("capital_antes", capital_inicial) for op in operaciones]) * 100, 2) if n and capital_inicial else 0.0,
        "profit_factor": _r(pf, 3),
        "max_drawdown_pct": round(max_drawdown(curva), 2), "duracion_dd_dias": duracion_drawdown(curva),
        "sharpe_diario": _r(sharpe_diario(curva, cfg["dia"]), 2),
        "rentabilidad_neta_pct": round((eq_final / capital_inicial - 1) * 100, 2) if capital_inicial else 0.0,
        "rentabilidad_anual_pct": round(((eq_final / capital_inicial) ** (365 / dias) - 1) * 100, 2) if dias >= 30 and capital_inicial and eq_final > 0 else None,
        "exposicion_pct": 0.0, "media_barras": round(barras / n, 1) if n else 0.0, "racha_perdedora_max": peor_racha,
        "comisiones_total": round(sum(op.get("comisiones", 0) for op in operaciones), 2),
        "deslizamiento_total": round(sum(op.get("deslizamiento", 0) for op in operaciones), 2),
        "paradas_dia": 0, "apagones": 0, "rechazadas": 0,
        "ic_expectativa_R": {"p5": _r(p5, 3), "p50": _r(p50, 3), "p95": _r(p95, 3)}, "t_R": _r(t_R, 2),
        "capital_inicial": round(capital_inicial, 2), "capital_final": round(eq_final, 2), "dias": round(dias, 1),
    }


def _completar_metricas(m, res):
    """Fields of the metrics that come from the engine's counters, not from the trade list."""
    m["exposicion_pct"] = round(res["barras_en_posicion"] / res["barras"] * 100, 2) if res.get("barras") else 0.0
    m["paradas_dia"] = res.get("paradas_dia", 0)
    m["apagones"] = res.get("apagones", 0)
    m["rechazadas"] = res.get("rechazadas", 0)
    m["comisiones_total"] = round(res.get("comisiones", m["comisiones_total"]), 2)
    m["deslizamiento_total"] = round(res.get("deslizamiento", m["deslizamiento_total"]), 2)
    return m


# ---------- walk-forward (§6.8) ----------

def ventanas(desde_t, hasta_t, is_dias, oos_dias, oos_min_dias=30):
    """IS of `is_dias` followed by an OOS of `oos_dias`, sliding by `oos_dias`; the last OOS may be shorter."""
    salida = []
    k = 0
    while True:
        a = desde_t + k * oos_dias * 86400
        b = a + is_dias * 86400
        if b >= hasta_t:
            break
        c = min(b + oos_dias * 86400, hasta_t)
        if c - b < oos_min_dias * 86400:
            break
        salida.append({"k": k + 1, "is": [a, b], "oos": [b, c]})
        k += 1
    return salida


def recorte(velas, desde_t, hasta_t, calentamiento):
    """velas[i0 - calentamiento : i1) with i0 the first bar at or after desde_t and i1 the first at or after hasta_t."""
    tiempos = [v[0] for v in velas]
    i0 = bisect_left(tiempos, desde_t)
    i1 = bisect_left(tiempos, hasta_t)
    return velas[max(0, i0 - int(calentamiento)):i1]


def _fila_is(params, r):
    pnls = [op["pnl"] for op in r["operaciones"]]
    Rs = [op["R"] for op in r["operaciones"]]
    return {"parametros": params, "n": len(Rs), "expectativa_R": round(statistics.mean(Rs), 3) if Rs else 0.0,
            "profit_factor": _r(_profit_factor(pnls), 3), "pnl": round(sum(pnls), 2), "apagones": r["apagones"]}


def _seleccionar(velas, estrategia, cfg, ventana, avisar=print, total=None):
    a, b = ventana["is"]
    k = ventana["k"]
    minimo = estrategia.min_ops_is if estrategia.min_ops_is is not None else cfg["min_operaciones_is"]
    rec = recorte(velas, a, b, estrategia.calentamiento)
    tabla = []
    ops_por_fila = []
    for params in estrategias.combinaciones(estrategia.rejilla, estrategia.defecto):
        r = simular(rec, estrategia, params, cfg, desde_t=a, hasta_t=b, capital=cfg["capital_inicial"])
        tabla.append(_fila_is(params, r))
        ops_por_fila.append(r["operaciones"])
    mejor = None
    for idx, fila in enumerate(tabla):
        if fila["apagones"] == 0 and fila["n"] >= minimo and (mejor is None or fila["expectativa_R"] > tabla[mejor]["expectativa_R"]):
            mejor = idx
    if mejor is None:
        params, por_defecto, elegida = dict(estrategia.defecto), True, 0
    else:
        params, por_defecto, elegida = tabla[mejor]["parametros"], False, mejor
    avisar(f"Ventana {k}/{total or '?'}: parámetros {params} ({tabla[elegida]['n']} operaciones dentro de muestra)"
           + (" · por defecto: ninguna combinación llegó al mínimo" if por_defecto else ""))
    return params, tabla, por_defecto, elegida, ops_por_fila[elegida] if ops_por_fila else []


def seleccionar(velas, estrategia, cfg, ventana, avisar=print, total=None):
    """Elige en la ventana IS la combinación de mayor expectativa_R sin apagados y con operaciones suficientes."""
    params, tabla, por_defecto, _, _ = _seleccionar(velas, estrategia, cfg, ventana, avisar, total)
    return params, tabla, por_defecto


def walk_forward(velas, estrategia, cfg, desde_t, hasta_t, avisar=print):
    """Ventana a ventana: parámetros elegidos en IS, juzgados en la OOS siguiente con el capital y el estado (pico,
    apagado, parada del día) arrastrados: una sola curva cosida y la regla 4 acumulada de verdad."""
    lista = ventanas(desde_t, hasta_t, cfg["ventana_is_dias"], cfg["ventana_oos_dias"], cfg["oos_min_dias"])
    capital = float(cfg["capital_inicial"])
    estado = None
    salida_v = []
    operaciones = []
    curva = []
    is_ops = []
    tot = {"apagones": 0, "paradas_dia": 0, "rechazadas": 0, "rechazos": {}, "comisiones": 0.0, "deslizamiento": 0.0,
           "barras": 0, "barras_en_posicion": 0}
    for w in lista:
        a, b = w["is"]
        b, c = w["oos"]
        params, tabla, por_defecto, elegida, ops_is = _seleccionar(velas, estrategia, cfg, w, avisar, len(lista))
        is_ops.extend(ops_is)
        r = simular(recorte(velas, b, c, estrategia.calentamiento), estrategia, params, cfg, desde_t=b, hasta_t=c,
                    capital=capital, ventana=w["k"], estado=estado)
        pnl = sum(op["pnl"] for op in r["operaciones"])
        Rs = [op["R"] for op in r["operaciones"]]
        salida_v.append({
            "k": w["k"], "is": [a, b], "oos": [b, c], "is_fechas": [_fecha_utc(a), _fecha_utc(b)],
            "oos_fechas": [_fecha_utc(b), _fecha_utc(c)], "parametros": params, "por_defecto": por_defecto,
            "is_tabla": tabla,
            "is_m": {"n": tabla[elegida]["n"], "expectativa_R": tabla[elegida]["expectativa_R"], "profit_factor": tabla[elegida]["profit_factor"]},
            "oos_m": {"n": len(Rs), "expectativa_R": round(statistics.mean(Rs), 3) if Rs else 0.0,
                      "profit_factor": _r(_profit_factor([op["pnl"] for op in r["operaciones"]]), 3), "pnl": round(pnl, 2),
                      "rentabilidad_pct": round((r["capital_final"] / capital - 1) * 100, 2) if capital else 0.0,
                      "apagones": r["apagones"], "paradas_dia": r["paradas_dia"], "capital_inicio": round(capital, 2),
                      "capital_final": round(r["capital_final"], 2)},
        })
        operaciones.extend(r["operaciones"])
        curva.extend(r["curva"])
        capital = r["capital_final"]
        estado = r["estado"]
        for k in ("apagones", "paradas_dia", "rechazadas", "comisiones", "deslizamiento", "barras", "barras_en_posicion"):
            tot[k] += r[k]
        for k, n in r["rechazos"].items():
            tot["rechazos"][k] = tot["rechazos"].get(k, 0) + n
    Rs_is = [op["R"] for op in is_ops]
    return {"ventanas": salida_v, "operaciones": operaciones, "curva": curva, "capital_final": capital, **tot,
            "is_total": {"n": len(Rs_is), "expectativa_R": round(statistics.mean(Rs_is), 3) if Rs_is else 0.0,
                         "profit_factor": _r(_profit_factor([op["pnl"] for op in is_ops]), 3)}}


# ---------- references: buy and hold, whole and at the 30 % cap (§6.9) ----------

def comprar_y_mantener(velas, cfg, desde_t, hasta_t, capital=None):
    """Compra al primer open y vende al último cierre, con los mismos costes; entera y con solo el 30 % (regla 5)."""
    com = cfg["comision_pct"] / 100
    desl = cfg["deslizamiento_pct"] / 100
    C = float(capital if capital is not None else cfg["capital_inicial"])
    tramo = [v for v in velas if desde_t <= v[0] < hasta_t]
    vacio = {"rentabilidad_neta_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_diario": None, "duracion_dd_dias": 0.0}
    if not tramo:
        return {"curva": [], "metricas": vacio, "al_30": {"curva": [], "rentabilidad_neta_pct": 0.0, "max_drawdown_pct": 0.0}}
    p0 = tramo[0][1]
    coste_unidad = p0 * (1 + desl) * (1 + com)
    q = C / coste_unidad
    q30 = 0.3 * C / coste_unidad
    curva = [[v[0], q * v[4]] for v in tramo]
    curva30 = [[v[0], 0.7 * C + q30 * v[4]] for v in tramo]
    c_fin = tramo[-1][4]
    final = q * c_fin * (1 - desl) * (1 - com)
    final30 = 0.7 * C + q30 * c_fin * (1 - desl) * (1 - com)
    return {"curva": curva,
            "metricas": {"rentabilidad_neta_pct": round((final / C - 1) * 100, 2),
                         "max_drawdown_pct": round(max_drawdown(curva), 2),
                         "sharpe_diario": _r(sharpe_diario(curva, cfg["dia"]), 2),
                         "duracion_dd_dias": duracion_drawdown(curva)},
            "al_30": {"curva": curva30, "rentabilidad_neta_pct": round((final30 / C - 1) * 100, 2),
                      "max_drawdown_pct": round(max_drawdown(curva30), 2)}}


# ---------- random entries with the strategy's own exit mechanics (§6.10) ----------

class EstrategiaAzar(Estrategia):
    """Entradas al azar con stop fijo en % y salida por tiempo: lo que haría alguien sin ninguna idea."""
    id = "azar"
    titulo = "Entradas al azar"
    descripcion = "Entradas aleatorias con la mecánica de salida de la estrategia (contraste)."
    calentamiento = 14
    defecto = {}
    rejilla = {}

    def __init__(self, marco, n_ops, stop_pct, barras, semilla):
        self.marco = marco
        self.n_ops = int(n_ops)
        self.stop_pct = float(stop_pct)
        self.barras = max(1, int(barras))
        self.semilla = semilla

    def preparar(self, velas, params, modo_dia="local"):
        n = len(velas)
        candidata = [0] * n
        if n > 14 and self.n_ops > 0:
            cuantas = min(n - 14, int(1.3 * self.n_ops) + 1)
            for i in random.Random(self.semilla).sample(range(14, n), cuantas):
                candidata[i] = 1
        return {"candidata": candidata}

    def senal(self, i, velas, ctx, pos, params, memoria):
        if pos is None:
            if ctx["candidata"][i] and memoria.get("entradas", 0) < self.n_ops:
                memoria["entradas"] = memoria.get("entradas", 0) + 1
                return {"accion": "comprar", "stop": velas[i][4] * (1 - self.stop_pct), "objetivo": None}
            return None
        if i - pos["entrada_i"] >= self.barras:
            return {"accion": "vender", "motivo": "tiempo"}
        return None


def _marco_de(velas):
    if len(velas) < 2:
        return 60
    return max(1, int(min(velas[k + 1][0] - velas[k][0] for k in range(min(len(velas) - 1, 50))) // 60))


def azar(velas, cfg, n_ops, stop_pct, barras, desde_t, hasta_t, semillas, expectativa_real, avisar=print):
    """`semillas` simulaciones de entradas aleatorias sobre el recorte OOS cosido; p_azar = (1 + #{E_R_s >= real})/(S+1)."""
    S = int(semillas)
    rec = recorte(velas, desde_t, hasta_t, 14)
    marco = _marco_de(rec)
    por_semilla = []
    if n_ops > 0 and rec:
        avisar(f"Contraste de azar: {S} semillas de {n_ops} entradas aleatorias (stop {_coma(stop_pct * 100, 2)} %, {barras} barras)…")
        for s in range(S):
            r = simular(rec, EstrategiaAzar(marco, n_ops, stop_pct, barras, s), {}, cfg, desde_t=desde_t, hasta_t=hasta_t,
                        capital=cfg["capital_inicial"])
            Rs = [op["R"] for op in r["operaciones"]]
            por_semilla.append({"semilla": s, "n": len(Rs), "expectativa_R": statistics.mean(Rs) if Rs else 0.0,
                                "rentabilidad_pct": (r["capital_final"] / cfg["capital_inicial"] - 1) * 100,
                                "profit_factor": _profit_factor([op["pnl"] for op in r["operaciones"]])})
    ers = [x["expectativa_R"] for x in por_semilla]
    rents = [x["rentabilidad_pct"] for x in por_semilla]
    pfs = [x["profit_factor"] for x in por_semilla if x["profit_factor"] is not None]
    if por_semilla:
        p_azar = (1 + sum(1 for e in ers if e >= expectativa_real)) / (S + 1)
        mejor = 100 * sum(1 for e in ers if e < expectativa_real) / S
    else:
        p_azar, mejor = None, None

    def tres(valores, d):
        return {"p5": _r(_percentil(valores, 0.05), d), "p50": _r(_percentil(valores, 0.50), d), "p95": _r(_percentil(valores, 0.95), d)}

    return {"semillas": S, "p_azar": p_azar, "mejor_que_pct": _r(mejor, 1), "expectativa_R": tres(ers, 3),
            "rentabilidad_pct": tres(rents, 2), "profit_factor": tres(pfs, 3), "por_semilla": por_semilla}


# ---------- gates, verdict, warnings (§6.11) ----------

def puertas(m, res, cfg):
    """Las siete comprobaciones fuera de muestra, en orden; cada una con ok, valor, umbral y texto."""
    u = cfg["puertas"]
    az = (res.get("referencias") or {}).get("azar") or {}
    vent = res.get("ventanas") or []
    n = m.get("n", 0)
    pf = m.get("profit_factor")
    mdd = m.get("max_drawdown_pct", 0.0)
    p = az.get("p_azar")
    positivas = sum(1 for w in vent if (w.get("oos_m") or {}).get("pnl", 0) > 0)
    ratio = positivas / len(vent) if vent else 0.0
    apagones = m.get("apagones", res.get("apagones", 0))
    e_r, e_pct = m.get("expectativa_R", 0.0), m.get("expectativa_pct", 0.0)
    if pf is None and n > 0:
        texto_pf = f"Profit factor: sin pérdidas en {n} operaciones · revisar, huele a mirar al futuro"
    else:
        texto_pf = f"Profit factor {_coma(pf, 2)} · > {_coma(u['profit_factor_min'], 1)}"
    if p is None:
        texto_azar = f"Frente al azar: sin operaciones · p < {_coma(u['p_azar_max'], 2)}"
    else:
        texto_azar = (f"Frente al azar: mejor que el {_coma(az.get('mejor_que_pct'), 1)} % de {az.get('semillas')} entradas aleatorias "
                      f"(p = {_coma(p, 4)}) · p < {_coma(u['p_azar_max'], 2)}")
    return {
        "expectativa": {"ok": bool(e_r > u["expectativa_min"] and e_pct > 0), "valor": e_r, "umbral": u["expectativa_min"],
                        "texto": f"Expectativa fuera de muestra: {_coma(e_r, 2, True)} R por operación ({_coma(e_pct, 2, True)} %) · tiene que ser > {_coma(u['expectativa_min'], 0)}"},
        "profit_factor": {"ok": bool(pf is not None and pf > u["profit_factor_min"]), "valor": pf, "umbral": u["profit_factor_min"], "texto": texto_pf},
        "drawdown": {"ok": bool(mdd < u["drawdown_max_pct"]), "valor": mdd, "umbral": u["drawdown_max_pct"],
                     "texto": f"Drawdown máximo {_coma(mdd, 1)} % · < {_coma(u['drawdown_max_pct'], 0)} % (con el apagado al −{_coma(cfg['apagado_pct'], 0)} % rara vez pasa del 13 %: léela junto a «apagados»)"},
        "operaciones": {"ok": bool(n > u["operaciones_min"]), "valor": n, "umbral": u["operaciones_min"],
                        "texto": f"Operaciones {n} · > {int(u['operaciones_min'])}"},
        "azar": {"ok": bool(p is not None and p < u["p_azar_max"]), "valor": p, "umbral": u["p_azar_max"], "texto": texto_azar},
        "consistencia": {"ok": bool(vent and ratio >= u["consistencia_min"]), "valor": round(ratio, 3), "umbral": u["consistencia_min"],
                         "texto": f"Ventanas con beneficio {positivas} de {len(vent)} · ≥ {_coma(u['consistencia_min'] * 100, 0)} %"},
        "apagado": {"ok": bool(apagones == 0), "valor": apagones, "umbral": 0,
                    "texto": f"Apagados por −{_coma(cfg['apagado_pct'], 0)} %: {apagones} · tiene que ser 0"},
    }


def veredicto(p, suficiente=True, motivo=""):
    if not suficiente:
        return {"clave": "insuficiente", "texto": "INSUFICIENTE: " + motivo, "motivos": [motivo]}
    motivos = [g["texto"] for g in p.values() if not g["ok"]]
    clave = "pasa" if not motivos else "no_pasa"
    return {"clave": clave, "texto": VEREDICTOS[clave], "motivos": motivos}


def avisos(res, cfg):
    """Avisos no bloqueantes sobre un resultado ya montado (§6.11)."""
    salida = []
    m = (res.get("oos") or {}).get("metricas") or {}
    vent = res.get("ventanas") or []
    is_t = res.get("is_total") or {}
    e_is, e_oos = is_t.get("expectativa_R"), m.get("expectativa_R")
    if e_is is not None and e_oos is not None and e_is > 0 and e_oos < 0.5 * e_is:
        salida.append(f"Dentro de muestra prometía {_coma(e_is, 2, True)} R; fuera dio {_coma(e_oos, 2, True)} R: menos de la mitad")
    if m.get("profit_factor") is not None and m["profit_factor"] > 3:
        salida.append("Profit factor > 3: sospechoso, revisa que no mire al futuro")
    if vent:
        b1, cK = vent[0]["oos"][0], vent[-1]["oos"][1]
        if cK - b1 < 365 * 86400:
            salida.append("Menos de 365 días fuera de muestra")
        if not (b1 < _epoch_fecha("2023-01-01") and cK > _epoch_fecha("2022-01-01")):
            salida.append("El rango fuera de muestra no incluye 2022 (año bajista): una estrategia long-only parece buena en alcista")
    rech = m.get("rechazadas", res.get("rechazadas", 0)) or 0
    if rech:
        salida.append(f"Órdenes rechazadas: {rech} (sin stop, stop demasiado cerca o bajo el mínimo)")
    defecto = sum(1 for w in vent if w.get("por_defecto"))
    if defecto:
        salida.append(f"Parámetros por defecto en {defecto} de {len(vent)} ventanas (pocas operaciones dentro de muestra para elegir)")
    if len(vent) >= 3:
        vistos = [repr(sorted(w.get("parametros", {}).items(), key=lambda kv: kv[0])) for w in vent]
        if len(set(vistos)) == len(vistos):
            salida.append("Parámetros distintos en cada ventana: nada estable")
    previos = res.get("intentos_previos", 0) or 0
    if previos >= 1:
        salida.append(f"Van {previos + 1} backtests de esta estrategia y par: cuantas más variantes pruebas, más fácil es que una pase por casualidad")
    return salida


# ---------- files (§6.13) ----------

def submuestrear(curva, n=PUNTOS_CURVA):
    """At most ~n points, always keeping the first and the last."""
    if len(curva) <= n:
        return [[t, round(e, 2)] for t, e in curva]
    paso = math.ceil(len(curva) / n)
    salida = [[t, round(e, 2)] for t, e in curva[::paso]]
    if salida[-1][0] != curva[-1][0]:
        salida.append([curva[-1][0], round(curva[-1][1], 2)])
    return salida


def _ruta_indice():
    return _carpeta() / "indice.json"


def _leer_indice():
    datos = mercado._leer_json(_ruta_indice(), {"lista": [], "pruebas_total": 0})
    if not isinstance(datos, dict):
        datos = {"lista": [], "pruebas_total": 0}
    datos["lista"] = [x for x in (datos.get("lista") or []) if isinstance(x, dict)]
    datos["pruebas_total"] = int(datos.get("pruebas_total") or 0)
    return datos


def _resumen_de(res):
    m = res["oos"]["metricas"]
    az = (res.get("referencias") or {}).get("azar") or {}
    return {"id": res["id"], "ts": res["ts"], "fecha": res["fecha"], "estrategia": res["estrategia"], "titulo": res["titulo"],
            "par": res["par"], "nombre_par": res["nombre_par"], "marco": res["marco"], "oos_desde": res["oos"]["desde"],
            "oos_hasta": res["oos"]["hasta"], "ventanas": len(res["ventanas"]), "n": m.get("n", 0),
            "expectativa_R": m.get("expectativa_R"), "expectativa_pct": m.get("expectativa_pct"),
            "profit_factor": m.get("profit_factor"), "max_drawdown_pct": m.get("max_drawdown_pct"),
            "sharpe_diario": m.get("sharpe_diario"), "p_azar": az.get("p_azar"),
            "veredicto": {"clave": res["oos"]["veredicto"]["clave"], "texto": res["oos"]["veredicto"]["texto"]},
            "intentos_previos": res.get("intentos_previos", 0)}


def guardar_resultado(res):
    """Escribe backtests/<id>.json y actualiza indice.json (pruebas_total += 1, últimos 200). Devuelve el id."""
    with mercado._lock:
        datos = _leer_indice()
        datos["pruebas_total"] += 1
        res["pruebas_total"] = datos["pruebas_total"]
        datos["lista"] = (datos["lista"] + [_resumen_de(res)])[-MAX_INDICE:]
        mercado._escribir_json(_carpeta() / f"{res['id']}.json", res)
        mercado._escribir_json(_ruta_indice(), datos)
    return res["id"]


def indice(n=50):
    """Resúmenes de los últimos backtests, el más nuevo primero."""
    return list(reversed(_leer_indice()["lista"]))[:max(0, int(n))]


def pruebas_total():
    return _leer_indice()["pruebas_total"]


def intentos_previos(estrategia_id, par):
    return sum(1 for x in _leer_indice()["lista"] if x.get("estrategia") == estrategia_id and x.get("par") == par)


def resultado(id):
    if not isinstance(id, str) or not RE_ID.fullmatch(id):
        return None
    ruta = _carpeta() / f"{id}.json"
    if not ruta.is_file():
        return None
    datos = mercado._leer_json(ruta, None)
    return datos if isinstance(datos, dict) else None


# ---------- the orchestrator (§6.13) ----------

def _tz():
    nombres = [x for x in time.tzname if x]
    return "/".join(dict.fromkeys(nombres)) if nombres else "?"


def _intervalo_origen(historico, par, marco):
    """Which stored interval cargar() picks for `marco` (§4.8), or None if it cannot be told."""
    try:
        res = historico.resumen().get(par) or {}
        candidatos = []
        for d in historico.INTERVALOS:
            if marco % d == 0 and str(d) in res and historico.meses(par, d):
                candidatos.append((res[str(d)].get("velas", 0) * d, -d, d))
        if not candidatos:
            return None
        return max(candidatos)[2]
    except Exception:   # the historico module may be older or the manifest odd: this is only informative
        return None


def correr(estrategia, par, desde=None, hasta=None, avisar=print, cfg=None, ahora=None, semillas=None):
    """Carga el histórico, corre el walk-forward, las referencias, el azar, las puertas y guarda el resultado."""
    from sala import historico
    cfg = _cfg(cfg)
    ahora = int(ahora if ahora is not None else time.time())
    est = estrategias.REGISTRO.get(estrategia) if isinstance(estrategia, str) else estrategia
    if est is None:
        raise ValueError(f"Estrategia desconocida: {estrategia}")
    marco = int(est.marco)
    nombre = mercado.nombre_par(par)
    rango = historico.rango_disponible(par, marco)
    if desde is None or hasta is None:
        if not rango:
            raise ValueError(f"No hay histórico de {nombre}: ejecuta python app.py historico en el PC")
    desde_t = _epoch_fecha(desde) if desde else int(rango[-2])
    hasta_t = _epoch_fecha(hasta) + 86400 if hasta else int(rango[-1])
    if hasta_t <= desde_t:
        raise ValueError("El rango está vacío: «hasta» tiene que ser posterior a «desde»")
    desde_txt, hasta_txt = _fecha_utc(desde_t), _fecha_utc(hasta_t - 1)
    origen = rango[0] if rango and len(rango) == 3 else _intervalo_origen(historico, par, marco)
    anos = (hasta_t - desde_t) / (365 * 86400)
    avisar(f"Cargando {_coma(anos, 1)} años de velas"
           + (f" de {origen} min y remuestreando a {marco} min…" if origen and origen != marco else f" de {marco} min…"))
    velas = historico.cargar(par, desde_t - (est.calentamiento + 10) * marco * 60, hasta_t, marco)
    utiles = [v for v in velas if desde_t <= v[0] < hasta_t]
    lista_v = ventanas(desde_t, hasta_t, cfg["ventana_is_dias"], cfg["ventana_oos_dias"], cfg["oos_min_dias"])
    huecos_largos = historico.huecos(utiles, marco, cfg["hueco_max_dias"] * 86400) if utiles else []
    avisar(f"{len(utiles)} velas de {marco} min entre {desde_txt} y {hasta_txt}; {len(lista_v)} ventanas fuera de muestra")

    motivo = ""
    if len(lista_v) < cfg["ventanas_minimas"]:
        motivo = f"hacen falta al menos {cfg['ventanas_minimas']} ventanas fuera de muestra (hay {len(lista_v)})"
    elif huecos_largos:
        h = max(huecos_largos, key=lambda x: x["minutos"])
        motivo = f"hueco de {round(h['minutos'] / 1440)} días en los datos ({_fecha_corta(h['desde'])} → {_fecha_corta(h['hasta'])})"
    elif len(utiles) < BARRAS_MINIMAS:
        motivo = f"hay menos de {BARRAS_MINIMAS} barras tras el calentamiento ({len(utiles)})"
    suficiente = not motivo
    previos = intentos_previos(est.id, par)
    capital = float(cfg["capital_inicial"])

    if suficiente:
        wf = walk_forward(velas, est, cfg, desde_t, hasta_t, avisar)
        m = _completar_metricas(metricas(wf["operaciones"], wf["curva"], capital, cfg), wf)
        b1, cK = wf["ventanas"][0]["oos"][0], wf["ventanas"][-1]["oos"][1]
        bh = comprar_y_mantener(velas, cfg, b1, cK, capital)
        ops = wf["operaciones"]
        n_ops = len(ops)
        stop_pct = statistics.median([(op["entrada"] - op["stop"]) / op["entrada"] for op in ops]) if ops else 0.01
        barras_med = int(statistics.median([op["barras"] for op in ops])) if ops else 1
        S = int(semillas if semillas is not None else cfg["semillas_azar"])
        az = azar(velas, cfg, n_ops, stop_pct, max(1, barras_med), b1, cK, S, m["expectativa_R"], avisar)
        az = {k: v for k, v in az.items() if k != "por_semilla"}
        az.update({"p_azar": _r(az["p_azar"], 4), "stop_pct": round(stop_pct * 100, 2), "barras": barras_med, "n_ops": n_ops})
        referencias = {"comprar_y_mantener": bh["metricas"], "comprar_y_mantener_30": {
            "rentabilidad_neta_pct": bh["al_30"]["rentabilidad_neta_pct"], "max_drawdown_pct": bh["al_30"]["max_drawdown_pct"]},
            "azar": az}
        curva, curva_bh, curva_bh_30 = wf["curva"], bh["curva"], bh["al_30"]["curva"]
        oos_desde, oos_hasta = _fecha_utc(b1), _fecha_utc(cK - 1)
    else:
        wf = {"ventanas": [], "operaciones": [], "curva": [], "capital_final": capital, "apagones": 0, "paradas_dia": 0,
              "rechazadas": 0, "rechazos": {}, "comisiones": 0.0, "deslizamiento": 0.0, "barras": 0, "barras_en_posicion": 0,
              "is_total": {"n": 0, "expectativa_R": 0.0, "profit_factor": None}}
        m = _completar_metricas(metricas([], [], capital, cfg), wf)
        referencias = {}
        curva, curva_bh, curva_bh_30 = [], [], []
        oos_desde = _fecha_utc(lista_v[0]["oos"][0]) if lista_v else desde_txt
        oos_hasta = _fecha_utc(lista_v[-1]["oos"][1] - 1) if lista_v else hasta_txt

    res = {
        "version": 1, "id": time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora)) + f"-{est.id}-{par}", "ts": ahora,
        "fecha": _fecha(ahora), "estrategia": est.id, "titulo": est.titulo, "par": par, "nombre_par": nombre, "marco": marco,
        "desde": desde_txt, "hasta": hasta_txt, "desde_t": desde_t, "hasta_t": hasta_t, "dia": cfg["dia"], "tz": _tz(),
        "cfg": cfg,
        "datos": {"barras": len(utiles), "intervalo_origen": origen, "remuestreado": bool(origen and origen != marco),
                  "huecos_largos": len(huecos_largos)},
        "ventanas": wf["ventanas"],
        "oos": {"metricas": m, "puertas": {}, "veredicto": {}, "avisos": [], "desde": oos_desde, "hasta": oos_hasta},
        "is_total": wf["is_total"], "referencias": referencias,
        "curva": submuestrear(curva), "curva_bh": submuestrear(curva_bh), "curva_bh_30": submuestrear(curva_bh_30),
        "costes": {"comisiones": round(wf["comisiones"], 2), "deslizamiento": round(wf["deslizamiento"], 2),
                   "comision_pct": cfg["comision_pct"], "deslizamiento_pct": cfg["deslizamiento_pct"],
                   "deslizamiento_stop_pct": cfg["deslizamiento_stop_pct"]},
        "operaciones": wf["operaciones"][-int(cfg["max_operaciones_guardadas"]):],
        "operaciones_total": len(wf["operaciones"]),
        "operaciones_truncadas": len(wf["operaciones"]) > cfg["max_operaciones_guardadas"],
        "pruebas_total": 0, "intentos_previos": previos, "aviso": AVISO_HONESTO,
    }
    if suficiente:
        res["oos"]["puertas"] = puertas(m, res, cfg)
        res["oos"]["veredicto"] = veredicto(res["oos"]["puertas"])
        res["oos"]["avisos"] = avisos(res, cfg)
    else:
        res["oos"]["veredicto"] = veredicto({}, False, motivo)
    guardar_resultado(res)
    v = res["oos"]["veredicto"]
    avisar(f"Veredicto: {v['texto']}")
    texto = (f"Backtest {est.titulo} {nombre} {marco} min {desde_txt}→{hasta_txt}: {v['texto']} "
             f"(E_R {_coma(m['expectativa_R'], 2, True)}, PF {_coma(m['profit_factor'], 2)}, MDD {_coma(m['max_drawdown_pct'], 1)} %, "
             f"{m['n']} op, p azar {_coma((referencias.get('azar') or {}).get('p_azar'), 4)})")
    try:
        mercado.anotar_diario("idea", texto, par=par, autor="cuant")
    except (ValueError, OSError):   # the journal never stops a backtest
        pass
    return res


def comprobar(inf, cfg=None):
    """Sección «Backtests» del chequeo: el último veredicto y cuántos backtests van. Nunca lanza."""
    inf.seccion("Backtests")
    try:
        lista = indice(1)
        if lista:
            u = lista[0]
            inf.ok("Último backtest", f"({u.get('titulo')} · {u.get('nombre_par')} · {u.get('fecha')} · {(u.get('veredicto') or {}).get('texto')})")
        else:
            inf.aviso("Último backtest", "(ninguno todavía)")
        inf.ok("Backtests hechos", f"({pruebas_total()})")
    except Exception as e:   # a broken index must not break the health page
        inf.error("Backtests", f"({e})")
