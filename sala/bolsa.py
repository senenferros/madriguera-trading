"""The easy front page: daily prices of a few traditional markets (Stooq's free CSV) plus BTC and ETH (the Kraken
history on disk), a traffic light per market that describes the STATE of the market in plain Spanish, and an honest
banner built from the backtest index.

Watch-only: nothing here buys, sells or recommends. The lights describe what the price has done, never what to do.

Files:
    datos/bolsa/<clave>.json   {"filas": [[fecha, cierre], ...], "descargado": ts, "intento": ts, "error": str}
"""
import csv
import io
import re
import statistics
import threading
import time
from datetime import date, datetime, timezone

import requests

import nucleo
from sala import mercado

STOOQ = "https://stooq.com/q/d/l/?s={simbolo}&i=d"
REFRESCO_S = 6 * 3600       # a market is downloaded again at most every 6 hours
FILAS_MAX = 420             # daily closes kept per market (a bit more than the 1-year + 200-day windows need)
_RE_CLAVE = re.compile(r"^[a-z0-9_]{1,30}$")
_lock = threading.Lock()

MERCADOS_DEFECTO = [
    {"clave": "sp500", "nombre": "S&P 500", "simbolo": "^spx", "moneda": "USD", "tipo": "indice"},
    {"clave": "ibex35", "nombre": "IBEX 35", "simbolo": "^ibex", "moneda": "EUR", "tipo": "indice"},
    {"clave": "oro", "nombre": "Oro", "simbolo": "xauusd", "moneda": "USD", "tipo": "metal"},
    {"clave": "plata", "nombre": "Plata", "simbolo": "xagusd", "moneda": "USD", "tipo": "metal"},
    {"clave": "brent", "nombre": "Petróleo Brent", "simbolo": "cb.f", "moneda": "USD", "tipo": "materia_prima"},
    {"clave": "tesla", "nombre": "Tesla", "simbolo": "tsla.us", "moneda": "USD", "tipo": "accion"},
]

# One line each for "¿Qué significa?" (by clave; unknown markets fall back to their type)
EXPLICACIONES = {
    "sp500": "El S&P 500: las 500 empresas más grandes de Estados Unidos juntas en un solo número.",
    "ibex35": "El IBEX 35: las 35 empresas más grandes de la Bolsa española juntas en un solo número.",
    "oro": "El oro: metal que la gente compra para guardar valor cuando desconfía de todo lo demás.",
    "plata": "La plata: metal precioso más barato que el oro, que también se usa mucho en la industria.",
    "brent": "El petróleo Brent: el precio de referencia del barril de petróleo en Europa.",
    "tesla": "Tesla: una sola empresa (coches eléctricos); una empresa sola se mueve mucho más que un índice.",
    "btc": "Bitcoin: la primera criptomoneda; dinero digital que sube y baja muchísimo.",
    "eth": "Ethereum: la segunda criptomoneda más grande; aún más movida que Bitcoin.",
}
EXPLICACION_TIPO = {"indice": "Un índice: muchas empresas juntas en un solo número.", "metal": "Un metal que se compra y se vende.",
                    "materia_prima": "Una materia prima.", "accion": "Una sola empresa.", "cripto": "Una criptomoneda."}

# ---------- traffic-light thresholds (also explained on the page, see UMBRALES_TEXTO) ----------
ROJO_CAIDA_MAXIMO_PCT = -20.0   # 20 % or more below its 1-year high -> rojo
VERDE_CAIDA_MAXIMO_PCT = -10.0  # verde needs it to be within 10 % of its 1-year high
MOVIDO_DIARIO_PCT = 2.5         # average daily move (absolute) over the last 20 sessions above this -> "se mueve mucho"
PENDIENTE_SESIONES = 20         # the 200-day average "rises" if it is higher than 20 sessions ago
MINIMO_CIERRES = 60             # fewer daily closes than this -> gris
MEDIA_LARGA = 200

UMBRALES_TEXTO = [
    ("verde", "Subida tranquila: el precio está por encima de su media de los últimos 200 días, esa media va hacia arriba, "
              "está a menos de un 10 % de su máximo del último año y no se mueve más de un 2,5 % al día de media."),
    ("amarillo", "Se mueve mucho o va sin rumbo: no cumple lo del verde ni lo del rojo (por ejemplo, sube pero dando "
                 "bandazos de más de un 2,5 % al día, o va de lado)."),
    ("rojo", "Cayendo fuerte: está un 20 % o más por debajo de su máximo del último año, o está por debajo de su media de "
             "200 días con esa media bajando y este mes en negativo."),
    ("gris", "Sin datos: todavía no hay suficientes precios guardados (hacen falta al menos 60 días)."),
]

# Patched by the tests: a fake Stooq (simbolo -> CSV text) instead of the network
descargar_fn = None


# ---------- config ----------

def mercados(cfg=None):
    """The traditional markets of config.yaml (section mercados_extra), or the defaults; invalid entries are skipped."""
    cfg = cfg if cfg is not None else nucleo.cargar_config()
    lista = cfg.get("mercados_extra")
    if not isinstance(lista, list) or not lista:
        return [dict(m) for m in MERCADOS_DEFECTO]
    out, vistos = [], set()
    for m in lista:
        if not isinstance(m, dict):
            continue
        clave, simbolo = str(m.get("clave", "")).strip().lower(), str(m.get("simbolo", "")).strip()
        if not _RE_CLAVE.fullmatch(clave) or not simbolo or clave in vistos:
            continue
        vistos.add(clave)
        out.append({"clave": clave, "nombre": str(m.get("nombre") or clave), "simbolo": simbolo,
                    "moneda": str(m.get("moneda") or "USD").upper(), "tipo": str(m.get("tipo") or "indice")})
    return out or [dict(m) for m in MERCADOS_DEFECTO]


# ---------- Stooq ----------

def parsear_csv(texto):
    """Stooq daily CSV (Date,Open,High,Low,Close,Volume) -> [[AAAA-MM-DD, close], ...] sorted, without duplicates.
    Raises ValueError when the text is not that CSV (Stooq answers 'No data' for an unknown symbol)."""
    texto = (texto or "").lstrip("﻿").strip()
    lector = csv.DictReader(io.StringIO(texto))
    campos = [c.strip().lower() for c in (lector.fieldnames or [])]
    if "date" not in campos or "close" not in campos:
        raise ValueError("Stooq no ha devuelto precios (" + (texto[:40].replace("\n", " ") or "respuesta vacía") + ")")
    filas = {}
    for fila in lector:
        fila = {str(k).strip().lower(): (v or "").strip() for k, v in fila.items() if k}
        try:
            dia = date.fromisoformat(fila["date"]).isoformat()
            cierre = float(fila["close"])
        except (KeyError, ValueError):
            continue
        if cierre > 0:
            filas[dia] = cierre
    if not filas:
        raise ValueError("Stooq no ha devuelto precios")
    return [[d, filas[d]] for d in sorted(filas)]


def _descargar(simbolo):
    if descargar_fn is not None:
        return descargar_fn(simbolo)
    r = requests.get(STOOQ.format(simbolo=simbolo), timeout=nucleo.TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def _ruta(clave):
    return nucleo.DATOS_DIR / "bolsa" / f"{clave}.json"


def cache(clave):
    return mercado._leer_json(_ruta(clave), {}) or {}


def actualizar(avisar=print, forzar=False, ahora=None, cfg=None):
    """Download every market whose cache is older than 6 h (or all, with forzar). A failure keeps the old cache and
    records the error. Returns {clave: "nuevo" | "reciente" | "error: ..."}."""
    ahora = ahora if ahora is not None else time.time()
    salida = {}
    for m in mercados(cfg):
        c = cache(m["clave"])
        if not forzar and c.get("filas") and ahora - (c.get("descargado") or 0) < REFRESCO_S:
            salida[m["clave"]] = "reciente"
            continue
        try:
            filas = parsear_csv(_descargar(m["simbolo"]))[-FILAS_MAX:]
            c = {"filas": filas, "descargado": ahora, "intento": ahora, "error": "", "simbolo": m["simbolo"]}
            salida[m["clave"]] = "nuevo"
            avisar(f"{m['nombre']}: {len(filas)} días, último {filas[-1][0]}")
        except (requests.RequestException, ValueError, RuntimeError, OSError) as err:
            # a network failure is reported by its kind only (the full proxy/URL text helps nobody on the page)
            motivo = type(err).__name__ if isinstance(err, requests.RequestException) else f"{type(err).__name__}: {err}"
            c = {**c, "intento": ahora, "error": motivo[:200]}
            salida[m["clave"]] = "error: " + c["error"]
            avisar(f"{m['nombre']}: sin datos nuevos ({c['error']})")
        with _lock:
            _ruta(m["clave"]).parent.mkdir(parents=True, exist_ok=True)
            mercado._escribir_json(_ruta(m["clave"]), c)
    return salida


# ---------- crypto from the Kraken history ----------

def cierres_cripto(par, ahora=None):
    """Daily closes [[AAAA-MM-DD (UTC), close], ...] of a Kraken pair from datos/historico (last ~420 days)."""
    try:
        from sala import historico
        ahora = int(ahora if ahora is not None else time.time())
        velas = historico.cargar(par, ahora - FILAS_MAX * 86400, ahora, marco=1440)
    except Exception:
        return []
    return [[datetime.fromtimestamp(v[0], timezone.utc).date().isoformat(), float(v[4])] for v in velas if v[4] > 0]


# ---------- the traffic light (pure) ----------

def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def _coma(x, d=1, signo=True):
    s = f"{x:+.{d}f}" if signo else f"{x:.{d}f}"
    return s.replace(".", ",")


def fmt_precio(x, moneda="USD"):
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    simbolo = {"EUR": "€", "USD": "$", "GBP": "£"}.get(moneda, moneda)
    return f"{s} {simbolo}"


def semaforo(filas, moneda="USD", nombre=""):
    """Pure. `filas` are daily closes [[AAAA-MM-DD, close], ...] in order. Returns
    {color, titular, datos: [str], numeros: {...}} where color is verde / amarillo / rojo / gris.
    The color describes the state of the market (see the thresholds at the top), never an instruction."""
    filas = [f for f in (filas or []) if f and f[1]]
    if len(filas) < MINIMO_CIERRES:
        return {"color": "gris", "titular": "Todavía no hay suficientes precios guardados para decir nada.",
                "datos": ([f"Último precio: {fmt_precio(filas[-1][1], moneda)} ({filas[-1][0]})"] if filas else []), "numeros": {}}
    fechas = [f[0] for f in filas]
    cierres = [f[1] for f in filas]
    precio, ultimo_dia = cierres[-1], fechas[-1]
    hoy = _pct(precio, cierres[-2])
    # this month / this year: against the last close of the previous month / year (or the first close we have)
    antes_mes = [c for d, c in filas if d[:7] < ultimo_dia[:7]]
    antes_ano = [c for d, c in filas if d[:4] < ultimo_dia[:4]]
    mes = _pct(precio, antes_mes[-1] if antes_mes else cierres[0])
    ano = _pct(precio, antes_ano[-1] if antes_ano else cierres[0])
    # 1-year high: the last 365 calendar days
    hace_un_ano = _menos_dias(ultimo_dia, 365)
    maximo = max(c for d, c in filas if d > hace_un_ano)
    desde_maximo = _pct(precio, maximo)
    n = min(MEDIA_LARGA, len(cierres) - PENDIENTE_SESIONES)
    media = statistics.fmean(cierres[-n:])
    media_antes = statistics.fmean(cierres[-n - PENDIENTE_SESIONES:-PENDIENTE_SESIONES])
    sube_media = media > media_antes
    encima = precio > media
    cambios = [abs(_pct(cierres[i], cierres[i - 1])) for i in range(len(cierres) - 20, len(cierres))]
    movido = statistics.fmean(cambios)

    if encima and sube_media:
        rumbo = "va hacia arriba este año"
    elif not encima and not sube_media:
        rumbo = "va hacia abajo este año"
    else:
        rumbo = "va de lado este año"

    if desde_maximo <= ROJO_CAIDA_MAXIMO_PCT or (not encima and not sube_media and mes < 0):
        color = "rojo"
        titular = (f"Está cayendo fuerte: un {_coma(-desde_maximo, 0, False)} % por debajo de su máximo del último año."
                   if desde_maximo <= ROJO_CAIDA_MAXIMO_PCT else "Está cayendo: por debajo de su media y bajando este mes.")
    elif encima and sube_media and desde_maximo > VERDE_CAIDA_MAXIMO_PCT and movido <= MOVIDO_DIARIO_PCT:
        color = "verde"
        titular = "Va subiendo con calma, cerca de su máximo del último año."
    else:
        color = "amarillo"
        if movido > MOVIDO_DIARIO_PCT:
            titular = f"Se está moviendo mucho: de media un {_coma(movido, 1, False)} % arriba o abajo cada día."
        else:
            titular = "Va sin un rumbo claro."

    datos = [
        f"Precio: {fmt_precio(precio, moneda)} (cierre del {_fecha_es(ultimo_dia)})",
        f"Hoy {_coma(hoy)} % · este mes {_coma(mes)} % · este año {_coma(ano)} %",
        f"En general {rumbo}; está a un {_coma(-desde_maximo, 0, False)} % de su máximo del último año"
        if desde_maximo < -0.5 else f"En general {rumbo}; está en su máximo del último año",
    ]
    return {"color": color, "titular": titular, "datos": datos,
            "numeros": {"precio": precio, "hoy_pct": hoy, "mes_pct": mes, "ano_pct": ano, "desde_maximo_pct": desde_maximo,
                        "media": media, "media_sube": sube_media, "movido_pct": movido, "fecha": ultimo_dia}}


def _menos_dias(dia, n):
    return date.fromordinal(date.fromisoformat(dia).toordinal() - n).isoformat()


def _fecha_es(dia):
    a, m, d = dia.split("-")
    return f"{d}/{m}/{a}"


# ---------- what the page shows ----------

def _cripto():
    out = []
    for par in mercado.configuracion()["pares"]:
        base = mercado.nombre_par(par).split("/")[0]
        clave = base.lower()
        out.append({"clave": clave, "nombre": {"BTC": "Bitcoin", "ETH": "Ethereum"}.get(base, base) + f" ({base})",
                    "moneda": par[-3:], "tipo": "cripto", "filas": cierres_cripto(par)})
    return out


def tarjetas(cfg=None, ahora=None):
    """One card per market: the traditional ones from the cache, then BTC and ETH from the Kraken history."""
    ahora = ahora if ahora is not None else time.time()
    out = []
    for m in mercados(cfg):
        c = cache(m["clave"])
        luz = semaforo(c.get("filas"), m["moneda"], m["nombre"])
        nota = ""
        if c.get("error"):
            nota = (f"Sin datos nuevos desde {time.strftime('%d/%m %H:%M', time.localtime(c['descargado']))}"
                    if c.get("descargado") else "Sin datos todavía: no se ha podido descargar") + " (fallo de conexión)."
        elif not c.get("filas"):
            nota = "Sin datos todavía: pulsa «Actualizar precios»."
        out.append({**m, **luz, "nota": nota, "explicacion": EXPLICACIONES.get(m["clave"]) or EXPLICACION_TIPO.get(m["tipo"], "")})
    for m in _cripto():
        luz = semaforo(m.pop("filas"), m["moneda"], m["nombre"])
        nota = "" if luz["color"] != "gris" else "Sin histórico de Kraken todavía: pulsa «Actualizar precios»."
        out.append({**m, **luz, "nota": nota, "explicacion": EXPLICACIONES.get(m["clave"]) or EXPLICACION_TIPO["cripto"]})
    return out


def banner(indice=None):
    """The honest answer to "¿hay algo que hacer hoy?", from the latest backtest per strategy and pair."""
    if indice is None:
        try:
            from sala import backtest
            indice = backtest.indice(10 ** 6)
        except Exception:
            indice = []
    ultimos = {}
    for r in indice or []:   # newest first: the first one seen per strategy x pair is the latest
        ultimos.setdefault((r.get("estrategia"), r.get("par")), r)
    aprobados = [r for r in ultimos.values() if (r.get("veredicto") or {}).get("clave") == "pasa"]
    if not aprobados:
        detalle = ("Todavía no se ha probado ninguna estrategia." if not ultimos
                   else f"Se han probado {len(ultimos)} combinaciones de estrategia y moneda, y ninguna ha aprobado.")
        return {"hay": False, "titulo": "¿Hay algo que hacer hoy? No.",
                "texto": "Estamos en simulación y ninguna estrategia ha aprobado el examen todavía. " + detalle}
    nombres = ", ".join(f"«{r.get('titulo') or r.get('estrategia')}» con {r.get('nombre_par') or r.get('par')}" for r in aprobados)
    return {"hay": True, "titulo": "¿Hay algo que hacer hoy? Nada con dinero.",
            "texto": f"Ha aprobado el examen: {nombres}. Lo único que haría es empezar a practicar con dinero ficticio "
                     "(Fase 2, paper trading) durante meses. Sigue siendo simulación: no hay que comprar ni vender nada."}


def ascii_(s):
    """Plain ASCII for the terminal: accents dropped, the euro sign spelled out."""
    import unicodedata
    s = str(s).replace("€", "EUR").replace("«", '"').replace("»", '"').replace("…", "...").replace("·", "-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def texto_cli(tarjeta):
    """ASCII-only lines for the terminal."""
    lineas = [f"[{tarjeta['color'].upper():8}] {ascii_(tarjeta['nombre'])}: {ascii_(tarjeta['titular'])}"]
    lineas += ["           " + ascii_(d) for d in tarjeta["datos"]]
    if tarjeta.get("nota"):
        lineas.append("           " + ascii_(tarjeta["nota"]))
    return lineas
