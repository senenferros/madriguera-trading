"""Las estrategias del analista cuantitativo: indicadores causales como funciones puras sobre listas (sin numpy),
el contrato `Estrategia`, las seis estrategias long-only de la Fase 1, las tres diarias de medio plazo, `EstrategiaFija` para las pruebas y el `REGISTRO`.

Nada aquí lee ficheros ni red. Toda serie devuelta por `preparar` está alineada por índice con `velas` y cada valor en
`i` depende solo de `velas[:i+1]` (`None` mientras no hay datos suficientes). La señal se decide al cierre de la barra
`i`; el motor (sala/backtest.py) la ejecuta al open de la barra siguiente.

Simulación · datos públicos · sin dinero real.
"""
import itertools
import statistics
from bisect import bisect_left, bisect_right, insort
from datetime import datetime, timezone

from sala import mercado


# ---------- time helpers (§2.3) ----------

def dia_de(t, modo="local"):
    """'AAAA-MM-DD' of the bar's open: UTC day, or the local day the watcher uses (mercado.dia_local)."""
    if modo == "utc":
        return datetime.fromtimestamp(t, timezone.utc).date().isoformat()
    return mercado.dia_local(t)


def hora_de(t, modo="local"):
    """Hour 0-23 of the bar's open in the chosen mode."""
    if modo == "utc":
        return (int(t) % 86400) // 3600
    return datetime.fromtimestamp(t).hour


# ---------- indicators: pure, causal, None while there is not enough data ----------

def ema(valores, n):
    """Exponential moving average: seed = SMA of the first n values, then alpha = 2/(n+1)."""
    n = int(n)
    salida = [None] * len(valores)
    if n <= 0 or len(valores) < n:
        return salida
    alpha = 2.0 / (n + 1)
    actual = sum(valores[:n]) / n
    salida[n - 1] = actual
    for i in range(n, len(valores)):
        actual = actual + alpha * (valores[i] - actual)
        salida[i] = actual
    return salida


def sma(valores, n):
    n = int(n)
    salida = [None] * len(valores)
    if n <= 0 or len(valores) < n:
        return salida
    acumulado = sum(valores[:n])
    salida[n - 1] = acumulado / n
    for i in range(n, len(valores)):
        acumulado += valores[i] - valores[i - n]
        salida[i] = acumulado / n
    return salida


def desviacion(valores, n):
    """Sample standard deviation (statistics.stdev) of the last n values."""
    n = int(n)
    salida = [None] * len(valores)
    if n < 2:
        return salida
    for i in range(n - 1, len(valores)):
        salida[i] = statistics.stdev(valores[i - n + 1:i + 1])
    return salida


def atr(velas, n=14):
    """Wilder's ATR: TR = max(h-l, |h-c_prev|, |l-c_prev|); first ATR = mean of the first n TR, then (prev*(n-1)+TR)/n."""
    n = int(n)
    salida = [None] * len(velas)
    if n <= 0 or len(velas) < n:
        return salida
    tr = []
    for i, v in enumerate(velas):
        if i == 0:
            tr.append(v[2] - v[3])
        else:
            c_prev = velas[i - 1][4]
            tr.append(max(v[2] - v[3], abs(v[2] - c_prev), abs(v[3] - c_prev)))
    actual = sum(tr[:n]) / n
    salida[n - 1] = actual
    for i in range(n, len(velas)):
        actual = (actual * (n - 1) + tr[i]) / n
        salida[i] = actual
    return salida


def rsi(cierres, n=14):
    """Wilder's RSI on closes; 100 when the average loss is 0."""
    n = int(n)
    salida = [None] * len(cierres)
    if n <= 0 or len(cierres) < n + 1:
        return salida
    ganancias = 0.0
    perdidas = 0.0
    for i in range(1, n + 1):
        d = cierres[i] - cierres[i - 1]
        if d > 0:
            ganancias += d
        else:
            perdidas -= d
    media_g, media_p = ganancias / n, perdidas / n
    salida[n] = 100.0 if media_p == 0 else 100.0 - 100.0 / (1 + media_g / media_p)
    for i in range(n + 1, len(cierres)):
        d = cierres[i] - cierres[i - 1]
        media_g = (media_g * (n - 1) + (d if d > 0 else 0.0)) / n
        media_p = (media_p * (n - 1) + (-d if d < 0 else 0.0)) / n
        salida[i] = 100.0 if media_p == 0 else 100.0 - 100.0 / (1 + media_g / media_p)
    return salida


def maximo_previo(valores, n):
    """max(valores[i-n:i]); None while i < n (the current bar is NOT included)."""
    n = int(n)
    salida = [None] * len(valores)
    if n <= 0:
        return salida
    for i in range(n, len(valores)):
        salida[i] = max(valores[i - n:i])
    return salida


def minimo_previo(valores, n):
    n = int(n)
    salida = [None] * len(valores)
    if n <= 0:
        return salida
    for i in range(n, len(valores)):
        salida[i] = min(valores[i - n:i])
    return salida


def mediana_previa(valores, n):
    """statistics.median(valores[i-n:i]) with a sorted sliding window (same numbers, much faster)."""
    n = int(n)
    salida = [None] * len(valores)
    if n <= 0 or len(valores) <= n:
        return salida
    ventana = sorted(valores[:n])
    medio = n // 2
    for i in range(n, len(valores)):
        if n % 2:
            salida[i] = ventana[medio]
        else:
            salida[i] = (ventana[medio - 1] + ventana[medio]) / 2
        # slide: drop valores[i-n], add valores[i]
        saliente = valores[i - n]
        del ventana[bisect_left(ventana, saliente)]
        insort(ventana, valores[i])
    return salida


def rango_dia(velas, modo_dia="local"):
    """(max_dia, min_dia, n_dia): highest high / lowest low / count of the EARLIER bars of the same day; None/0 on the
    first bar of each day."""
    max_dia, min_dia, n_dia = [], [], []
    dia_actual = None
    mx = mn = None
    n = 0
    for v in velas:
        d = dia_de(v[0], modo_dia)
        if d != dia_actual:
            dia_actual, mx, mn, n = d, None, None, 0
        max_dia.append(mx)
        min_dia.append(mn)
        n_dia.append(n)
        mx = v[2] if mx is None else max(mx, v[2])
        mn = v[3] if mn is None else min(mn, v[3])
        n += 1
    return max_dia, min_dia, n_dia


def alinear(superior, t):
    """Index of the last bar of a higher timeframe already CLOSED at t (t_sup + marco_sup*60 <= t), or None."""
    velas_sup, marco_sup = superior
    tiempos = [v[0] for v in velas_sup]
    k = bisect_right(tiempos, t - marco_sup * 60) - 1
    return k if k >= 0 else None


# ---------- the contract ----------

class Estrategia:
    """Una estrategia long-only: `preparar` calcula sus series (alineadas con `velas`) y `senal` decide al cierre de `i`.

    `senal` devuelve None · {"accion": "comprar", "stop": float, "objetivo": float|None} (solo con `pos is None`) ·
    {"accion": "vender", "motivo": "senal"|"tiempo"} · {"stop": float} (stop móvil: el motor solo lo sube).
    """
    id = ""
    titulo = ""
    descripcion = ""
    marco = 60
    calentamiento = 0
    defecto = {}
    rejilla = {}
    min_ops_is = None   # None -> cfg['min_operaciones_is']
    familia = None      # attempts are counted per family (None -> the id): a daily variant counts as one more try of its idea

    def preparar(self, velas, params, modo_dia="local"):
        return {}

    def senal(self, i, velas, ctx, pos, params, memoria):
        return None


class EstrategiaFija(Estrategia):
    """Guion de órdenes para las pruebas: un dict {i: senal} o un callable con la firma de `senal`."""
    id = "fija"
    titulo = "Guion"
    descripcion = "Órdenes fijadas de antemano (solo pruebas)."
    marco = 60
    calentamiento = 0
    defecto = {}
    rejilla = {}

    def __init__(self, ordenes, marco=60, calentamiento=0, ctx=None):
        self._ordenes = ordenes
        self.marco = marco
        self.calentamiento = calentamiento
        self._ctx = ctx
        self.defecto = {}
        self.rejilla = {}

    def preparar(self, velas, params, modo_dia="local"):
        return self._ctx(velas) if callable(self._ctx) else (self._ctx or {})

    def senal(self, i, velas, ctx, pos, params, memoria):
        if callable(self._ordenes):
            return self._ordenes(i, velas, ctx, pos, params, memoria)
        return self._ordenes.get(i)


# ---------- the six strategies (§5.4) ----------

def _atr_ok(valor):
    return valor is not None and valor > 0


class RoturaDia(Estrategia):
    id = "rotura_dia"
    titulo = "Rotura del máximo del día"
    descripcion = "Compra cuando el cierre supera el máximo del día y sale a las 22 h; una entrada por día, como la alerta del vigía."
    marco = 15
    calentamiento = 110   # 14 (ATR) + one day of 15-min bars
    defecto = {"atr_k": 1.5, "minimo_barras": 4}
    rejilla = {"atr_k": [1.5, 2.5], "minimo_barras": [4, 8]}

    def preparar(self, velas, params, modo_dia="local"):
        max_dia, min_dia, n_dia = rango_dia(velas, modo_dia)
        return {"atr14": atr(velas, 14), "max_dia": max_dia, "min_dia": min_dia, "n_dia": n_dia,
                "hora": [hora_de(v[0], modo_dia) for v in velas], "dia": [dia_de(v[0], modo_dia) for v in velas]}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        if pos is None:
            a = ctx["atr14"][i]
            mx = ctx["max_dia"][i]
            # no entries from 21 h on: the exit fires on the first bar at or after 22 h, so a late breakout would be a
            # one-bar round trip paying both commissions, or (on the day's last bar) a 22 h hold on yesterday's range
            if (memoria.get("dia_entrada") != ctx["dia"][i] and ctx["n_dia"][i] >= params["minimo_barras"]
                    and ctx["hora"][i] < 21 and mx is not None and c > mx and _atr_ok(a)):
                memoria["dia_entrada"] = ctx["dia"][i]
                return {"accion": "comprar", "stop": c - params["atr_k"] * a, "objetivo": None}
            return None
        dia_entrada = ctx["dia"][pos["entrada_i"]]
        if ctx["dia"][i] != dia_entrada or ctx["hora"][i] >= 22:
            return {"accion": "vender", "motivo": "senal"}
        return None


class PicoVolumen(Estrategia):
    id = "pico_volumen"
    titulo = "Pico de volumen con vela alcista"
    descripcion = "Compra tras una vela alcista con volumen muy por encima de la mediana y sale por tiempo."
    marco = 5
    calentamiento = 49
    defecto = {"factor": 3, "salida_barras": 12}
    rejilla = {"factor": [3, 5], "salida_barras": [12, 24]}

    def preparar(self, velas, params, modo_dia="local"):
        mediana_vol = mediana_previa([v[5] for v in velas], 48)
        ratio = [None if (m is None or m <= 0) else v[5] / m for v, m in zip(velas, mediana_vol)]
        return {"mediana_vol": mediana_vol, "ratio": ratio, "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        v = velas[i]
        if pos is None:
            m = ctx["mediana_vol"][i]
            a = ctx["atr14"][i]
            # the same floating-point test as mercado.pico_volumen (not v/m >= factor, which differs by one ulp on
            # exact multiples); `ratio` stays in ctx for display
            if (m is not None and m > 0 and not (v[5] < params["factor"] * m) and v[4] > v[1] and i > 0 and v[4] > velas[i - 1][4]
                    and i - memoria.get("i_entrada", -10 ** 9) >= 6 and _atr_ok(a)):
                memoria["i_entrada"] = i
                return {"accion": "comprar", "stop": min(v[3], v[4] - 1.0 * a), "objetivo": None}
            return None
        if i - pos["entrada_i"] >= params["salida_barras"]:
            return {"accion": "vender", "motivo": "tiempo"}
        return None


class CruceMedias(Estrategia):
    id = "cruce_medias"
    titulo = "Cruce de medias con stop móvil"
    descripcion = "Compra cuando la media rápida cruza por encima de la lenta y sigue con un stop móvil de ATR."
    marco = 240
    calentamiento = 240   # 3 x the slowest EMA
    defecto = {"medias": [10, 40], "atr_k": 2}
    rejilla = {"medias": [[10, 40], [20, 80]], "atr_k": [2, 3]}
    min_ops_is = 8

    def preparar(self, velas, params, modo_dia="local"):
        c = [v[4] for v in velas]
        rapida, lenta = params["medias"]
        return {"ema_r": ema(c, rapida), "ema_l": ema(c, lenta), "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        r, l, a = ctx["ema_r"][i], ctx["ema_l"][i], ctx["atr14"][i]
        if r is None or l is None or not _atr_ok(a):
            return None
        if pos is None:
            if i == 0:
                return None
            r0, l0 = ctx["ema_r"][i - 1], ctx["ema_l"][i - 1]
            if r0 is not None and l0 is not None and r0 <= l0 and r > l and c > l:
                return {"accion": "comprar", "stop": c - params["atr_k"] * a, "objetivo": None}
            return None
        if r < l:
            return {"accion": "vender", "motivo": "senal"}
        return {"stop": c - params["atr_k"] * a}


class Donchian(Estrategia):
    id = "donchian"
    titulo = "Rotura de canal (tortuga)"
    descripcion = "Compra al superar el máximo de n barras; el canal inferior y el ATR hacen de stop."
    marco = 240
    calentamiento = 69   # 55 + 14
    defecto = {"n": 20, "atr_k": 2}
    rejilla = {"n": [20, 55], "atr_k": [2, 3]}
    min_ops_is = 8

    def preparar(self, velas, params, modo_dia="local"):
        n = int(params["n"])
        return {"max_n": maximo_previo([v[2] for v in velas], n), "min_m": minimo_previo([v[3] for v in velas], n // 2),
                "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        a = ctx["atr14"][i]
        if not _atr_ok(a):
            return None
        if pos is None:
            mx = ctx["max_n"][i]
            if mx is not None and c > mx:
                return {"accion": "comprar", "stop": c - params["atr_k"] * a, "objetivo": None}
            return None
        mn = ctx["min_m"][i]
        nivel = c - params["atr_k"] * a
        return {"stop": max(mn, nivel) if mn is not None else nivel}


class Bandas(Estrategia):
    id = "bandas"
    titulo = "Reversión a la media en tendencia"
    descripcion = "Compra bajo la banda inferior cuando el precio sigue por encima de la EMA 200 y sale en la media."
    marco = 60
    calentamiento = 200
    defecto = {"k": 2.0, "salida_barras": 24}
    rejilla = {"k": [2.0, 2.5], "salida_barras": [24, 48]}

    def preparar(self, velas, params, modo_dia="local"):
        c = [v[4] for v in velas]
        return {"sma20": sma(c, 20), "desv20": desviacion(c, 20), "ema200": ema(c, 200), "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        m, d, e, a = ctx["sma20"][i], ctx["desv20"][i], ctx["ema200"][i], ctx["atr14"][i]
        if pos is None:
            if m is None or d is None or e is None or not _atr_ok(a):
                return None
            if c < m - params["k"] * d and c > e:
                return {"accion": "comprar", "stop": c - 2 * a, "objetivo": None}
            return None
        if m is not None and c >= m:
            return {"accion": "vender", "motivo": "senal"}
        if i - pos["entrada_i"] >= params["salida_barras"]:
            return {"accion": "vender", "motivo": "tiempo"}
        return None


class RSI(Estrategia):
    id = "rsi"
    titulo = "RSI sobrevendido con filtro"
    descripcion = "Compra con RSI bajo el umbral si el precio está sobre la EMA 200 y sale cuando el RSI se recupera."
    marco = 15
    calentamiento = 200
    defecto = {"umbral": 30, "salida_rsi": 50}
    rejilla = {"umbral": [25, 30], "salida_rsi": [50, 60]}

    def preparar(self, velas, params, modo_dia="local"):
        c = [v[4] for v in velas]
        return {"rsi14": rsi(c, 14), "ema200": ema(c, 200), "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        r, e, a = ctx["rsi14"][i], ctx["ema200"][i], ctx["atr14"][i]
        if pos is None:
            if r is None or e is None or not _atr_ok(a):
                return None
            if r < params["umbral"] and c > e:
                return {"accion": "comprar", "stop": c - 1.5 * a, "objetivo": None}
            return None
        if r is not None and r > params["salida_rsi"]:
            return {"accion": "vender", "motivo": "senal"}
        if i - pos["entrada_i"] >= 32:
            return {"accion": "vender", "motivo": "tiempo"}
        return None


# ---------- daily medium-term strategies (marco 1440) ----------
#
# PRE-REGISTERED before any run on real data (2026-10-07). Grids are fixed here and are not to be tuned after
# seeing results; any change is a new attempt and must be counted as such (intentos_previos counts per family).
#   donchian_dia      n in {20, 55} (breakout of the n-day high), m in {10, 20} (exit on the m-day low) -> 4 combos
#   cruce_medias_dia  SMA 50/200 and 20/100; initial protective stop 3 x ATR(14); exit on the cross down -> 2 combos
#   rebote_minimo     the owner's video idea, see the class docstring; retroceso in {0.382, 0.5}, m in {10, 20}
# Long only, spot, the five risk rules applied by the engine as for every other strategy.

class DonchianDia(Estrategia):
    id = "donchian_dia"
    familia = "donchian"
    titulo = "Rotura de canal diaria (tortuga)"
    descripcion = "Compra al cerrar por encima del máximo de n días; sale al perder el mínimo de m días (stop que solo sube)."
    marco = 1440
    calentamiento = 56
    defecto = {"n": 20, "m": 10}
    rejilla = {"n": [20, 55], "m": [10, 20]}
    min_ops_is = 4

    def preparar(self, velas, params, modo_dia="local"):
        return {"max_n": maximo_previo([v[2] for v in velas], int(params["n"]))}

    def senal(self, i, velas, ctx, pos, params, memoria):
        c = velas[i][4]
        # the m-day low INCLUDING today (min of the previous m+1 bars shifted by one -> bars i-m..i)
        mn = min(v[3] for v in velas[max(0, i - int(params["m"]) + 1):i + 1])
        if pos is None:
            mx = ctx["max_n"][i]
            if mx is not None and c > mx and mn < c:
                return {"accion": "comprar", "stop": mn, "objetivo": None}
            return None
        return {"stop": mn}


class CruceMediasDia(Estrategia):
    id = "cruce_medias_dia"
    familia = "cruce_medias"
    titulo = "Cruce de medias diario (50/200)"
    descripcion = "Compra cuando la media simple rápida cruza por encima de la lenta (cruce dorado) y vende en el cruce contrario."
    marco = 1440
    calentamiento = 201
    defecto = {"medias": [50, 200]}
    rejilla = {"medias": [[50, 200], [20, 100]]}
    min_ops_is = 1

    def preparar(self, velas, params, modo_dia="local"):
        c = [v[4] for v in velas]
        rapida, lenta = params["medias"]
        return {"sma_r": sma(c, rapida), "sma_l": sma(c, lenta), "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        r, l, a = ctx["sma_r"][i], ctx["sma_l"][i], ctx["atr14"][i]
        if r is None or l is None or i == 0:
            return None
        if pos is None:
            r0, l0 = ctx["sma_r"][i - 1], ctx["sma_l"][i - 1]
            if r0 is not None and l0 is not None and r0 <= l0 and r > l and _atr_ok(a):
                return {"accion": "comprar", "stop": velas[i][4] - 3 * a, "objetivo": None}
            return None
        if r < l:
            return {"accion": "vender", "motivo": "senal"}
        return None


def pivotes(valores, k, tipo):
    """Indices j where valores[j] is a strict pivot (max for 'alto', min for 'bajo') of valores[j-k:j+k+1]: strictly
    beyond the k values on its left and at least as extreme as the k on its right. A pivot at j is only KNOWN at
    bar j + k; callers must respect that."""
    salida = []
    for j in range(k, len(valores) - k):
        x = valores[j]
        izq, der = valores[j - k:j], valores[j + 1:j + k + 1]
        if tipo == "alto" and all(x > y for y in izq) and all(x >= y for y in der):
            salida.append(j)
        elif tipo == "bajo" and all(x < y for y in izq) and all(x <= y for y in der):
            salida.append(j)
    return salida


class ReboteMinimo(Estrategia):
    """Rebote rápido tras un mínimo más bajo (idea del vídeo que mandó el dueño), formalizado así y fijado de antemano:

    1. Pivotes de k = 3 barras a cada lado; un pivote en j solo se conoce en la barra j + 3.
    2. Tramo de caída: desde el último pivote alto conocido H (índice h) hasta el mínimo más bajo B (índice b) de las
       barras h+1..i-1. Barras de caída = b - h.
    3. Mínimo más bajo: low[b] < el último pivote bajo anterior a h (el mínimo de oscilación previo).
    4. La caída es de verdad: high[h] - low[b] >= 3 x ATR(14).
    5. (B es el primero de los mínimos iguales.) Rebote rápido: el cierre de hoy recupera al menos `retroceso` de la caída (low[b] + r·(high[h] - low[b])) y lo
       hace en menos barras de las que tardó en caer: i - b < b - h. Una entrada por cada mínimo B.
    6. Stop bajo el nuevo mínimo: low[b] - 0,5 x ATR(14); después sube al mínimo de m días; salida por tiempo a las
       40 barras.
    """
    id = "rebote_minimo"
    titulo = "Rebote rápido tras un mínimo más bajo"
    descripcion = ("Tras una caída que marca un mínimo por debajo del anterior, compra si el precio rebota más deprisa de lo "
                   "que cayó; stop bajo el nuevo mínimo, salida en el mínimo de m días o a las 40 sesiones.")
    marco = 1440
    calentamiento = 30
    defecto = {"retroceso": 0.5, "m": 10}
    rejilla = {"retroceso": [0.382, 0.5], "m": [10, 20]}
    min_ops_is = 3
    K = 3
    SALIDA_BARRAS = 40

    def preparar(self, velas, params, modo_dia="local"):
        k = self.K

        def conocido(indices):
            # [i] -> index of the most recent pivot already confirmed at bar i (j + k <= i), or None
            salida, p, actual = [None] * len(velas), 0, None
            for i in range(len(velas)):
                while p < len(indices) and indices[p] + k <= i:
                    actual = indices[p]
                    p += 1
                salida[i] = actual
            return salida

        return {"ultimo_alto": conocido(pivotes([v[2] for v in velas], k, "alto")),
                "ultimo_bajo": conocido(pivotes([v[3] for v in velas], k, "bajo")), "atr14": atr(velas, 14)}

    def senal(self, i, velas, ctx, pos, params, memoria):
        a = ctx["atr14"][i]
        c = velas[i][4]
        if pos is not None:
            if i - pos["entrada_i"] >= self.SALIDA_BARRAS:
                return {"accion": "vender", "motivo": "tiempo"}
            m = int(params["m"])
            return {"stop": min(v[3] for v in velas[max(0, i - m + 1):i + 1])}
        h = ctx["ultimo_alto"][i]
        if h is None or not _atr_ok(a) or i - h < 3:
            return None
        # the lowest low of h+1..i-1 (the earliest one on ties: a rebound bar that only matches the low does not
        # restart the rebound count)
        b = None
        for j in range(h + 1, i):
            if b is None or velas[j][3] < velas[b][3]:
                b = j
        if b is None or memoria.get("b_usado") == b:
            return None
        # the most recent pivot low before h: known at bar h + k - 1 (index <= h - 1), and h + k - 1 < i
        previo = ctx["ultimo_bajo"][h + self.K - 1]
        if previo is None:
            return None
        minimo, alto = velas[b][3], velas[h][2]
        caida_barras, rebote_barras = b - h, i - b
        if not (minimo < velas[previo][3] and alto - minimo >= 3 * a and rebote_barras < caida_barras):
            return None
        if c >= minimo + params["retroceso"] * (alto - minimo):
            memoria["b_usado"] = b
            return {"accion": "comprar", "stop": minimo - 0.5 * a, "objetivo": None}
        return None


# ---------- registry and parameter grids (§5.5) ----------

def combinaciones(rejilla, defecto):
    """[defecto] + the product of the grid in key order, without repeating any dict; at most 4 combinations."""
    salida = [dict(defecto)]
    claves = list(rejilla.keys())
    for valores in itertools.product(*[rejilla[k] for k in claves]):
        d = dict(zip(claves, valores))
        if d not in salida:
            salida.append(d)
    assert len(salida) <= 4, f"rejilla demasiado grande: {len(salida)} combinaciones"
    return salida


REGISTRO = {"rotura_dia": RoturaDia(), "pico_volumen": PicoVolumen(), "cruce_medias": CruceMedias(),
            "donchian": Donchian(), "bandas": Bandas(), "rsi": RSI(),
            "donchian_dia": DonchianDia(), "cruce_medias_dia": CruceMediasDia(), "rebote_minimo": ReboteMinimo()}


def familia(est):
    return getattr(est, "familia", None) or est.id


def lista():
    """Lo que la página pinta en el desplegable, en el orden del registro."""
    return [{"id": e.id, "titulo": e.titulo, "marco": e.marco, "descripcion": e.descripcion,
             "rejilla": e.rejilla, "defecto": e.defecto, "familia": familia(e)} for e in REGISTRO.values()]
