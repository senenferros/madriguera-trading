"""Histórico diario largo de los mercados tradicionales (Yahoo Finance) para el backtest de medio plazo.

`python app.py historico yahoo` baja, para cada mercado de `config.yaml → mercados_backtest`, todas las velas diarias
que Yahoo tenga (OHLCV completo, no solo cierres como sala/bolsa.py) y las guarda con `historico.guardar` como velas
de 1440 minutos bajo un código de par válido (SPX500, IBEX35, OROUSD…), fuente «yahoo». Así `historico.cargar` y
`backtest.correr` las leen igual que las de Kraken.

Nada de esto se ha podido ejecutar contra Yahoo desde el contenedor de desarrollo: se prueba con el gancho
`descargar_fn` y el dueño lo ejecuta en su PC. Solo lectura de precios públicos; nada mueve dinero.
"""
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

import nucleo

# range=max first; if Yahoo downgrades it to weekly/monthly bars, ask again with explicit dates from 1970
URL_MAX = "https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}?range=max&interval=1d&events=split"
URL_FECHAS = "https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}?period1=0&period2={hasta}&interval=1d&events=split"
FUENTE = "yahoo"

MERCADOS_DEFECTO = [
    {"par": "SPX500", "nombre": "S&P 500", "simbolo": "^GSPC", "moneda": "USD"},
    {"par": "IBEX35", "nombre": "IBEX 35", "simbolo": "^IBEX", "moneda": "EUR"},
    {"par": "OROUSD", "nombre": "Oro", "simbolo": "GC=F", "moneda": "USD"},
    {"par": "PLATAUSD", "nombre": "Plata", "simbolo": "SI=F", "moneda": "USD"},
    {"par": "BRENTUSD", "nombre": "Petróleo Brent", "simbolo": "BZ=F", "moneda": "USD"},
    {"par": "TSLAUSD", "nombre": "Tesla", "simbolo": "TSLA", "moneda": "USD"},
]

# Patched by the tests: simbolo -> Yahoo chart JSON (dict), instead of the network
descargar_fn = None


def mercados(cfg=None):
    """The markets of config.yaml → mercados_backtest (par, nombre, simbolo, moneda), or the defaults."""
    from sala import historico
    cfg = cfg if cfg is not None else nucleo.cargar_config()
    lista = cfg.get("mercados_backtest") if isinstance(cfg, dict) else None
    if not isinstance(lista, list) or not lista:
        return [dict(m) for m in MERCADOS_DEFECTO]
    out, vistos = [], set()
    for m in lista:
        if not isinstance(m, dict):
            continue
        try:
            par = historico._par_valido(m.get("par"))
        except ValueError:
            continue
        simbolo = str(m.get("simbolo") or "").strip()
        if not simbolo or par in vistos:
            continue
        vistos.add(par)
        out.append({"par": par, "nombre": str(m.get("nombre") or par), "simbolo": simbolo,
                    "moneda": str(m.get("moneda") or "USD").upper()})
    return out or [dict(m) for m in MERCADOS_DEFECTO]


def pares(cfg=None):
    return [m["par"] for m in mercados(cfg)]


def nombre(par, cfg=None):
    """Human name of a Yahoo market code, or None when `par` is not one (then it is a Kraken pair)."""
    for m in mercados(cfg):
        if m["par"] == par:
            return m["nombre"]
    return None


def parsear_chart(datos):
    """Yahoo chart JSON (interval=1d) -> daily candles [t, o, h, l, c, v] with t = 00:00 UTC of the exchange's
    calendar day (historico needs t % 86400 == 0). Rows with a missing or non-positive close are dropped; a missing
    open/high/low is filled from the close and high/low are widened to contain open and close (old index data has
    O = H = L = C or a zero open). Raises ValueError when the answer is not daily prices."""
    try:
        res = datos["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        meta = res.get("meta") or {}
    except (KeyError, IndexError, TypeError):
        raise ValueError("Yahoo no ha devuelto precios")
    gran = meta.get("dataGranularity")
    if gran not in (None, "1d"):
        raise ValueError(f"Yahoo ha devuelto velas de {gran}, no diarias")
    offset = int(meta.get("gmtoffset") or 0)
    n = len(ts)

    def col(nombre_col):
        c = q.get(nombre_col) or []
        return list(c) + [None] * (n - len(c))

    o_, h_, l_, c_, v_ = col("open"), col("high"), col("low"), col("close"), col("volume")
    por_dia = {}
    for k, t in enumerate(ts):
        c = c_[k]
        if not isinstance(c, (int, float)) or c <= 0 or t is None:
            continue
        o = o_[k] if isinstance(o_[k], (int, float)) and o_[k] > 0 else c
        h = h_[k] if isinstance(h_[k], (int, float)) and h_[k] > 0 else max(o, c)
        l = l_[k] if isinstance(l_[k], (int, float)) and l_[k] > 0 else min(o, c)
        h, l = max(h, o, c), min(l, o, c)
        vol = float(v_[k]) if isinstance(v_[k], (int, float)) and v_[k] >= 0 else 0.0
        local = int(t) + offset
        dia = local - local % 86400
        por_dia[dia] = [dia, float(o), float(h), float(l), float(c), vol]
    if not por_dia:
        raise ValueError("Yahoo no ha devuelto precios")
    velas = [por_dia[d] for d in sorted(por_dia)]
    if len(velas) >= 20:
        pasos = sorted(velas[k + 1][0] - velas[k][0] for k in range(len(velas) - 1))
        if pasos[len(pasos) // 2] > 4 * 86400:
            raise ValueError("Yahoo ha devuelto velas semanales o mensuales, no diarias")
    return velas


def _descargar(simbolo):
    if descargar_fn is not None:
        return parsear_chart(descargar_fn(simbolo))
    cab = {"User-Agent": "Mozilla/5.0"}
    s = quote(simbolo, safe="")
    r = requests.get(URL_MAX.format(simbolo=s), timeout=nucleo.TIMEOUT * 2, headers=cab)
    r.raise_for_status()
    try:
        return parsear_chart(r.json())
    except ValueError:
        r = requests.get(URL_FECHAS.format(simbolo=s, hasta=int(time.time())), timeout=nucleo.TIMEOUT * 2, headers=cab)
        r.raise_for_status()
        return parsear_chart(r.json())


def actualizar(pares_sel=None, avisar=print, cfg=None):
    """Download and store the daily history of each market (all of mercados_backtest, or `pares_sel`). A market
    that fails is reported and skipped. Returns {par: n_velas | "error: ..."}."""
    from sala import historico
    salida = {}
    lista = mercados(cfg)
    if pares_sel:
        quiero = {historico._par_valido(p) for p in pares_sel}
        lista = [m for m in lista if m["par"] in quiero]
    for m in lista:
        try:
            velas = _descargar(m["simbolo"])
            # the last candle may be today's session still trading: keep only closed UTC days
            hoy = int(time.time()) // 86400 * 86400
            velas = [v for v in velas if v[0] < hoy]
            n = historico.guardar(m["par"], 1440, velas, fuente=FUENTE)
            salida[m["par"]] = n
            if velas:
                avisar(f"{m['nombre']} ({m['par']}): {n} días, {_fecha(velas[0][0])} → {_fecha(velas[-1][0])}")
        except (requests.RequestException, ValueError, OSError) as err:
            motivo = type(err).__name__ if isinstance(err, requests.RequestException) else str(err)
            salida[m["par"]] = "error: " + motivo[:200]
            avisar(f"{m['nombre']} ({m['par']}): sin datos ({motivo[:200]})")
    return salida


def _fecha(t):
    return datetime.fromtimestamp(t, timezone.utc).date().isoformat()
