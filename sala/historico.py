"""Histórico de velas de Kraken en datos/historico/<par>/<N>m/<AAAA-MM>.json, para el backtest.

Tres fuentes, todas públicas y sin clave:
    OHLC   (cola)      GET /0/public/OHLC: como máximo las 720 velas más recientes de cada intervalo.
    Trades (relleno)   GET /0/public/Trades: 1000 operaciones por llamada, cursor en nanosegundos, reanudable;
                       se agregan a velas de 1 minuto. Un día de BTC/EUR son 20–60 llamadas.
    CSV    (años)      los ficheros OHLCVT trimestrales de Kraken (XBTEUR_1.csv, XBTEUR_60.csv…), importados en streaming.

Convención del 1 minuto: no existen velas de volumen 0 (la API OHLC las inventa; el CSV y Trades las omiten).
Lector de rangos con remuestreo mes a mes, huecos, validación cruzada, manifiesto (estado.json), bloqueo entre
procesos (ocupado.json con latido) y sección «Histórico» del comprobador. Nada aquí mueve dinero.

Nada de esto se ha podido ejecutar contra Kraken desde el contenedor de desarrollo: se prueba con los ganchos
`mercado.descargar_fn` y `historico.descargar_trades_fn`, y el dueño lo ejecuta en su PC.
"""
import csv
import math
import os
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

import nucleo
from sala import mercado

INTERVALOS = (1, 5, 15, 30, 60, 240, 720, 1440)   # minutes; the CSV ships 1,5,15,60,240,720,1440; OHLC adds 30
KRAKEN_TRADES = "https://api.kraken.com/0/public/Trades"
descargar_trades_fn = None      # tests: (par, desde_ns: int) -> (trades: list, last_ns: str)
ESPERAS = (5, 10, 20, 40, 60, 60)                  # backoff after 429 / EAPI:Rate limit / EService:* / RequestException
LATIDO_MAX = 180                                   # seconds; an older heartbeat means the other process died
MAX_DIAS_VIGIA = 400                               # consolidated watcher days remembered in estado.json
_RE_MES = re.compile(r"^\d{4}-\d{2}$")
_RE_CSV = re.compile(r"^([A-Za-z0-9]{6,12})_(\d+)$")
_RETRY = ("429", "EAPI:Rate limit", "EService:Unavailable", "EService:Busy", "EGeneral:Temporary", "EService:Market in")
_propios = {}                                      # pair -> {"ruta", "inicio"}: locks held by this process (reentrant)


class Ocupado(RuntimeError):
    """Another process holds datos/historico/<par>/ocupado.json."""


# ---------- configuration, paths, time ----------

CONFIG_DEFECTO = {"intervalos": [1, 60, 1440], "dias_trades": 90, "max_llamadas_panel": 300, "volcar_cada": 50,
                  "hueco_min_minutos": 60}


def configuracion(cfg=None):
    """The 'historico' section of config.yaml validated with defaults; works with the whole config, with the bare
    section or with an already validated dict, and when the section is missing altogether."""
    cfg = cfg if cfg is not None else nucleo.cargar_config()
    sec = cfg.get("historico") if isinstance(cfg, dict) and isinstance(cfg.get("historico"), dict) else cfg
    sec = sec if isinstance(sec, dict) else {}

    def entero(clave, minimo, maximo=10 ** 9):
        try:
            v = int(sec.get(clave) if sec.get(clave) is not None else CONFIG_DEFECTO[clave])
        except (TypeError, ValueError):
            v = CONFIG_DEFECTO[clave]
        return max(minimo, min(maximo, v))

    intervalos = []
    for x in (sec.get("intervalos") if isinstance(sec.get("intervalos"), (list, tuple)) else CONFIG_DEFECTO["intervalos"]):
        try:
            x = int(x)
        except (TypeError, ValueError):
            continue
        if x in INTERVALOS and x not in intervalos:
            intervalos.append(x)
    return {"intervalos": sorted(intervalos) or list(CONFIG_DEFECTO["intervalos"]),
            "dias_trades": entero("dias_trades", 1, 3650),
            "max_llamadas_panel": entero("max_llamadas_panel", 1),
            "volcar_cada": entero("volcar_cada", 1),
            "hueco_min_minutos": entero("hueco_min_minutos", 1)}


def _carpeta_hist():
    return nucleo.DATOS_DIR / "historico"   # read at call time: the tests move DATOS_DIR


def ruta_mes(par, intervalo, mes):
    return _carpeta_hist() / par / f"{int(intervalo)}m" / f"{mes}.json"


def _ruta_estado():
    return _carpeta_hist() / "estado.json"


def mes_de(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m")


def fecha_utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def _t_mes(mes):
    """First second (UTC) of the month "AAAA-MM"."""
    a, m = int(mes[:4]), int(mes[5:7])
    return int(datetime(a, m, 1, tzinfo=timezone.utc).timestamp())


def _mes_siguiente(mes):
    a, m = int(mes[:4]), int(mes[5:7])
    return f"{a + (m == 12):04d}-{(m % 12) + 1:02d}"


def _meses_entre(desde_t, hasta_t):
    """The UTC months touched by [desde_t, hasta_t)."""
    out, mes, fin = [], mes_de(desde_t), mes_de(max(desde_t, hasta_t - 1))
    while mes <= fin:
        out.append(mes)
        mes = _mes_siguiente(mes)
    return out


def _t_de_fecha(texto):
    """'AAAA-MM-DD' -> epoch of that UTC midnight (ValueError if malformed)."""
    d = date.fromisoformat(str(texto).strip())
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _fecha_hora(t):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(t))


def _ns(valor):
    """A trade time (float seconds, or the string Kraken sends) -> integer nanoseconds without float noise."""
    try:
        return int(Decimal(str(valor)) * 10 ** 9)
    except (InvalidOperation, ValueError, TypeError):
        return int(float(valor) * 1e9)


def _numero(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validar_vela(v, intervalo):
    """[t, open, high, low, close, volume]: t int aligned to the interval, finite numbers, low <= o,c <= high, low > 0."""
    if not isinstance(v, (list, tuple)) or len(v) != 6:
        return False
    t, o, h, l, c, vol = v
    if isinstance(t, bool) or not isinstance(t, int) or t < 0 or t >= 10 ** 11 or t % (int(intervalo) * 60) != 0:
        return False   # t >= 1e11 is a millisecond timestamp or nonsense: mes_de() would overflow on it
    if not all(_numero(x) for x in (o, h, l, c, vol)):
        return False
    return l > 0 and l <= min(o, c) and max(o, c) <= h and vol >= 0


def _normalizar(v):
    """Numbers as they come from a CSV or the API -> the candle format (None when hopeless)."""
    try:
        t = v[0]
        if isinstance(t, float) and t.is_integer():
            t = int(t)
        elif isinstance(t, str):
            t = int(float(t))
        return [t, float(v[1]), float(v[2]), float(v[3]), float(v[4]), float(v[5])]
    except (TypeError, ValueError, IndexError):
        return None


def _dormir(segundos):
    """Kraken asks for about a second between public calls; the fake Trades hook (tests) does not need the wait."""
    if descargar_trades_fn is None:
        time.sleep(segundos)


# ---------- the manifest datos/historico/estado.json ----------

def _leer_estado():
    est = mercado._leer_json(_ruta_estado(), {})
    return est if isinstance(est, dict) else {}


def _entrada(est, par, intervalo):
    e = est.setdefault(par, {}).setdefault(str(int(intervalo)), {})
    e.setdefault("actualizado", None)
    e.setdefault("fuentes", [])
    e.setdefault("dias_vigia", [])
    e.setdefault("huecos_verificados", [])
    e.setdefault("meses", {})
    return e


def _modificar_estado(fn):
    """Read-modify-write of estado.json under the process lock; fn(est) edits it in place."""
    with mercado._lock:
        est = _leer_estado()
        fn(est)
        mercado._escribir_json(_ruta_estado(), est)
    return est


def _resumen_mes(velas, intervalo):
    return {"primero_t": velas[0][0], "ultimo_t": velas[-1][0], "velas": len(velas),
            "huecos": [[h["desde"], h["hasta"]] for h in huecos(velas, intervalo)]}


def _mtime(ruta):
    try:
        return int(ruta.stat().st_mtime)
    except OSError:
        return int(time.time())


# ---------- candles on disk: one file per pair, interval and UTC month ----------

def _par_valido(par):
    """Pair codes are upper case on disk, in the manifest and in Kraken's answers: 'xbteur' and 'XBTEUR' must be one key."""
    par = str(par or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,12}", par):
        raise ValueError(f"Par no válido: «{par or '?'}» (ejemplo: XBTEUR)")
    return par


def guardar(par, intervalo, velas, fuente="api"):
    """Merge candles into their month files (dict by t, the newer wins), drop invalid ones, refresh the manifest.
    Returns the number of valid candles merged (not necessarily new). Two sources are special: 'ohlc' never stores a
    candle that has not closed yet (Kraken's answer ends with the frame in progress) and 'vigia' never replaces a
    candle already stored (the watcher's copy is the least reliable one)."""
    intervalo = int(intervalo)
    por_mes = {}
    limite = int(time.time()) if fuente == "ohlc" else None
    for v in velas:
        v = _normalizar(v)
        if v is None or not validar_vela(v, intervalo):
            continue
        if limite is not None and v[0] + intervalo * 60 > limite:
            continue
        por_mes.setdefault(mes_de(v[0]), {})[v[0]] = v
    total = 0
    for mes in sorted(por_mes):
        lote = por_mes[mes]
        ruta = ruta_mes(par, intervalo, mes)
        with mercado._lock:
            actuales = {v[0]: v for v in mercado._leer_json(ruta, []) if validar_vela(v, intervalo)}
            if fuente == "vigia":
                lote = {t: v for t, v in lote.items() if t not in actuales}
            actuales.update(lote)
            lista = [actuales[t] for t in sorted(actuales)]
            mercado._escribir_json(ruta, lista)
            marca = _mtime(ruta)

            def cambio(est, mes=mes, lista=lista, marca=marca):
                e = _entrada(est, par, intervalo)
                e["meses"][mes] = _resumen_mes(lista, intervalo)
                e["actualizado"] = max(e.get("actualizado") or 0, marca)
                if fuente and fuente not in e["fuentes"]:
                    e["fuentes"].append(fuente)
            _modificar_estado(cambio)
        total += len(lote)
    return total


def meses(par, intervalo):
    carpeta = _carpeta_hist() / par / f"{int(intervalo)}m"
    if not carpeta.is_dir():
        return []
    return sorted(r.stem for r in carpeta.glob("*.json") if _RE_MES.match(r.stem))


def leer_mes(par, intervalo, mes):
    velas = mercado._leer_json(ruta_mes(par, intervalo, mes), [])
    return velas if isinstance(velas, list) else []


def remuestrear(velas, origen, destino, hasta_t=None):
    """Pure. Candles of `origen` minutes (sorted) -> candles of `destino` minutes (destino multiple of origen).
    A bucket is emitted only when it closes at or before `hasta_t` (default: the end of the last source candle)."""
    origen, destino = int(origen), int(destino)
    if origen <= 0 or destino <= 0 or destino % origen != 0:
        raise ValueError(f"No se puede remuestrear de {origen} a {destino} minutos")
    if not velas:
        return []
    paso = destino * 60
    limite = hasta_t if hasta_t is not None else velas[-1][0] + origen * 60
    out = []
    actual = None
    for v in velas:
        b = v[0] - v[0] % paso
        if actual is None or b != actual[0]:
            if actual is not None:
                out.append(actual)
            actual = [b, v[1], v[2], v[3], v[4], v[5]]
        else:
            if v[2] > actual[2]:
                actual[2] = v[2]
            if v[3] < actual[3]:
                actual[3] = v[3]
            actual[4] = v[4]
            actual[5] += v[5]
    out.append(actual)
    return [c for c in out if c[0] + paso <= limite]


def _fuente_para(par, marco, desde_t=None, hasta_t=None):
    """The stored interval cargar() reads for `marco` (§4.8): widest coverage velas·d, ties to the finest. Given a
    range, an interval whose month files cover every month of it beats one with a month missing: a hole the Trades
    backfill filled at 1 min must not hide behind the wider 60-min series. Returns ((completo, cobertura), d, r)."""
    marco = int(marco)
    res = resumen().get(par) or {}
    necesarios = set(_meses_entre(desde_t, hasta_t)) if desde_t is not None and hasta_t is not None and hasta_t > desde_t else set()
    mejor = None
    for d in INTERVALOS:
        if marco % d != 0:
            continue
        presentes = meses(par, d)
        if not presentes:
            continue
        r = res.get(str(d)) or {}
        clave = (1 if necesarios and necesarios <= set(presentes) else 0, (r.get("velas") or 0) * d)
        if mejor is None or clave > mejor[0]:
            mejor = (clave, d, r)
    return mejor


def rango_disponible(par, marco=1):
    """(desde_t, hasta_t) of the source interval cargar() would use for `marco`, or None."""
    f = _fuente_para(par, marco)
    if not f or not f[2]:
        return None
    return (f[2]["desde_t"], f[2]["hasta_t"])


def cargar(par, desde_t, hasta_t, marco=1):
    """Candles of `marco` minutes with desde_t <= t and t + marco*60 <= hasta_t, sorted, without duplicates.
    Resamples month by month, so only one source month plus the output live in memory."""
    marco = int(marco)
    f = _fuente_para(par, marco, desde_t, hasta_t)
    if not f or hasta_t <= desde_t:
        return []
    d = f[1]
    m0, m1 = mes_de(desde_t), mes_de(max(desde_t, hasta_t - 1))
    out = {}
    todos = meses(par, d)
    for mes in todos:
        if mes < m0 or mes > m1:
            continue
        velas = leer_mes(par, d, mes)
        if d != marco:
            # a hasta_t beyond the end of the data must not close the last bucket early: where the source really ends,
            # the bucket in progress is not emitted (inside the data a missing minute is "no trades", not an end)
            limite = min(hasta_t, velas[-1][0] + d * 60) if velas and mes == todos[-1] else hasta_t
            velas = remuestrear(velas, d, marco, hasta_t=limite)
        for v in velas:
            if v[0] >= desde_t and v[0] + marco * 60 <= hasta_t:
                out[v[0]] = v
        del velas
    return [out[t] for t in sorted(out)]


def huecos(velas, intervalo, minimo_s=3600):
    """Pure: jumps between consecutive candles longer than the interval by at least `minimo_s` seconds."""
    paso = int(intervalo) * 60
    out = []
    for a, b in zip(velas, velas[1:]):
        salto = b[0] - a[0]
        if salto > paso and salto - paso >= minimo_s:
            out.append({"desde": a[0] + paso, "hasta": b[0], "minutos": (salto - paso) // 60})
    return out


# ---------- Trades: download, aggregate, resumable backfill ----------

def agregar_trades(trades, intervalo=1):
    """Pure: Kraken trade rows [price, volume, time, ...] (strings or floats) -> candles of `intervalo` minutes.
    Minutes without trades do not exist; sorted by t."""
    paso = int(intervalo) * 60
    velas = {}
    for fila in trades:
        try:
            precio, vol, t = float(fila[0]), float(fila[1]), int(float(fila[2]))
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(precio) and math.isfinite(vol)) or precio <= 0 or vol < 0:
            continue
        m = t - t % paso
        v = velas.get(m)
        if v is None:
            velas[m] = [m, precio, precio, precio, precio, vol]
        else:
            if precio > v[2]:
                v[2] = precio
            if precio < v[3]:
                v[3] = precio
            v[4] = precio
            v[5] += vol
    return [velas[m] for m in sorted(velas)]


def _fusionar_minuto(acum, velas):
    """Merge freshly aggregated minutes into the buffer: open stays, high/low extend, close is the latest, volume adds."""
    for v in velas:
        a = acum.get(v[0])
        if a is None:
            acum[v[0]] = list(v)
        else:
            a[2] = max(a[2], v[2])
            a[3] = min(a[3], v[3])
            a[4] = v[4]
            a[5] += v[5]


def descargar_trades(par, desde_ns):
    """One page of public trades since the nanosecond cursor -> (rows, last). Raises RuntimeError("Kraken: ...") on an
    HTTP or API error and requests.RequestException on network trouble."""
    if descargar_trades_fn is not None:
        return descargar_trades_fn(par, desde_ns)
    r = requests.get(KRAKEN_TRADES, params={"pair": par, "since": str(int(desde_ns)), "count": 1000}, timeout=nucleo.TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Kraken: HTTP {r.status_code} {r.reason}")
    datos = r.json()
    if datos.get("error"):
        raise RuntimeError("Kraken: " + "; ".join(str(e) for e in datos["error"]))
    resultado = datos.get("result") or {}
    clave = next((k for k in resultado if k != "last"), None)
    if not clave:
        raise RuntimeError(f"Kraken: sin operaciones para {par}")
    return list(resultado[clave] or []), str(resultado.get("last") or desde_ns)


def _reintentable(err):
    if isinstance(err, requests.RequestException):
        return True
    texto = str(err)
    return any(clave in texto for clave in _RETRY)


def _trade_id(fila):
    try:
        return int(fila[6]) if len(fila) > 6 and fila[6] not in ("", None) else None
    except (TypeError, ValueError):
        return None


def _ruta_cursor(par):
    return _carpeta_hist() / par / "cursor_trades.json"


def rellenar_con_trades(par, desde_t, hasta_t, avisar=print, max_llamadas=None, cfg=None):
    """Backfill the 1-minute history of [desde_t, hasta_t) from the public Trades endpoint, resumable through
    cursor_trades.json. Network trouble never raises: the buffer is flushed, the cursor saved and 'terminado' is False.
    Returns {'velas', 'llamadas', 'hasta_t', 'terminado', 'sin_operaciones'}."""
    cfg = configuracion(cfg)
    desde_t, hasta_t = int(desde_t), int(hasta_t)
    nombre = mercado.nombre_par(par)
    ruta_cursor = _ruta_cursor(par)
    cursor = mercado._leer_json(ruta_cursor, None)
    if hasta_t <= desde_t:
        return {"velas": 0, "llamadas": 0, "hasta_t": desde_t, "terminado": True, "sin_operaciones": True}
    total_velas, llamadas_previas = 0, 0
    ultimo_id = None
    if isinstance(cursor, dict) and cursor.get("desde_t") == desde_t and cursor.get("hasta_t") == hasta_t and cursor.get("last"):
        if cursor.get("pendiente_t"):
            # the pending minute of the last flush was never stored: download it again from its first trade
            since = int(cursor["pendiente_t"]) * 10 ** 9 - 1
        else:
            since = int(cursor["last"])
            try:   # the trade of the cursor comes back with the first page: its id is what tells it apart
                ultimo_id = int(cursor["ultimo_id"]) if cursor.get("ultimo_id") is not None else None
            except (TypeError, ValueError):
                ultimo_id = None
        total_velas = int(cursor.get("velas") or 0)
        llamadas_previas = int(cursor.get("llamadas") or 0)
        avisar(f"Trades {nombre}: reanudo el tramo {_fecha_hora(desde_t)} → {_fecha_hora(hasta_t)} desde el cursor ({total_velas} velas ya guardadas)")
    else:
        since = desde_t * 10 ** 9 - 1
    acum = {}                      # minute t -> candle still in memory
    estado = {"llamadas": 0, "last": str(since), "ultimo_min": None, "hasta": desde_t, "terminado": False, "operaciones": 0,
              "velas": total_velas, "ultimo_id": ultimo_id}
    mes_actual = None

    def volcar(final):
        completas = sorted(t for t in acum if t + 60 <= hasta_t and (final or (estado["ultimo_min"] is not None and t < estado["ultimo_min"])))
        if completas:
            lote = [acum.pop(t) for t in completas]
            estado["velas"] += guardar(par, 1, lote, fuente="trades")
            estado["hasta"] = max(estado["hasta"], completas[-1] + 60)
        if estado["terminado"]:
            try:
                ruta_cursor.unlink()
            except OSError:
                pass
        else:
            pendiente = min(acum) if acum else None
            mercado._escribir_json(ruta_cursor, {"desde_t": desde_t, "hasta_t": hasta_t, "last": estado["last"],
                                                 "pendiente_t": pendiente, "velas": estado["velas"], "ultimo_id": estado["ultimo_id"],
                                                 "llamadas": llamadas_previas + estado["llamadas"], "actualizado": int(time.time())})

    fallos = 0
    with ocupar(par):
        try:
            while True:
                if max_llamadas is not None and estado["llamadas"] >= max_llamadas:
                    avisar(f"Trades {nombre}: tope de {max_llamadas} llamadas en esta ejecución; el cursor queda guardado")
                    break
                if estado["llamadas"]:
                    _dormir(1.1)
                latir(par)
                try:
                    trades, last = descargar_trades(par, since)
                except (requests.RequestException, RuntimeError, ValueError) as err:
                    if not _reintentable(err):
                        avisar(f"Trades {nombre}: {err}; el cursor queda guardado")
                        break
                    fallos += 1
                    if fallos >= len(ESPERAS) + 1:
                        avisar("Kraken no responde; el cursor queda guardado, relanza más tarde")
                        break
                    espera = ESPERAS[min(fallos - 1, len(ESPERAS) - 1)]
                    avisar(f"Trades {nombre}: {type(err).__name__ if isinstance(err, requests.RequestException) else err}; reintento en {espera} s")
                    _dormir(espera)
                    continue
                fallos = 0
                estado["llamadas"] += 1
                nuevas = []
                for fila in trades:
                    tid = _trade_id(fila)
                    try:
                        t_ns = _ns(fila[2])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if tid is not None and estado["ultimo_id"] is not None:
                        # Kraken repeats the trade of the cursor: the id tells it apart. The time cannot: `time` is a
                        # float with ~240 ns of noise while `last` is exact, and several fills of one sweep share a
                        # time, so a time filter at the cursor would drop real trades. Only clearly older rows go.
                        if tid <= estado["ultimo_id"] or t_ns < since - 10 ** 7:
                            continue
                    elif t_ns <= since:
                        continue
                    nuevas.append(fila)
                try:
                    last_ns = int(str(last))
                except (TypeError, ValueError):
                    last_ns = since
                llego_al_final = any(int(float(f[2])) >= hasta_t for f in nuevas)
                utiles = [f for f in nuevas if int(float(f[2])) < hasta_t]
                if utiles:
                    estado["operaciones"] += len(utiles)
                    _fusionar_minuto(acum, agregar_trades(utiles, 1))
                    estado["ultimo_min"] = int(float(utiles[-1][2])) // 60 * 60
                    estado["ultimo_id"] = _trade_id(utiles[-1])
                if not nuevas or last_ns <= since or llego_al_final:
                    estado["terminado"] = True
                    estado["last"] = str(max(last_ns, since))
                    break
                since = last_ns
                estado["last"] = str(last_ns)
                mes = mes_de(estado["ultimo_min"]) if estado["ultimo_min"] is not None else mes_actual
                if estado["llamadas"] % cfg["volcar_cada"] == 0 or (mes_actual is not None and mes != mes_actual):
                    volcar(False)
                    avisar(f"Trades {nombre}: hasta {_fecha_hora(estado['ultimo_min'])} · {estado['velas']} velas · {llamadas_previas + estado['llamadas']} llamadas")
                mes_actual = mes
        finally:   # Ctrl+C included: the buffer is flushed once and the cursor saved (terminado is False then)
            volcar(estado["terminado"])
    return {"velas": estado["velas"], "llamadas": estado["llamadas"], "hasta_t": estado["hasta"], "terminado": estado["terminado"],
            "sin_operaciones": estado["terminado"] and estado["velas"] == 0 and estado["operaciones"] == 0}


# ---------- the watcher's day files -> history ----------

def consolidar_vigia(par, avisar=print):
    """Copy the CLOSED local days of datos/velas/<par>/<día>.json into the 1-minute history (volume-0 candles dropped).
    Read-only on datos/velas; days already consolidated (estado.json) are skipped. Returns candles merged."""
    if mercado.configuracion()["intervalo"] != 1:
        avisar(f"Vigía de {mercado.nombre_par(par)}: sus velas no son de 1 minuto, no se consolidan")
        return 0
    carpeta = mercado._carpeta() / "velas" / par
    if not carpeta.is_dir():
        return 0
    hoy = date.today().isoformat()
    hechos = set(_entrada(_leer_estado(), par, 1).get("dias_vigia") or [])
    dias = sorted(r.stem for r in carpeta.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.stem) and r.stem < hoy and r.stem not in hechos)
    if not dias:
        return 0
    total = 0
    for k, dia in enumerate(dias):
        if k % 20 == 0:
            latir(par)   # hundreds of day files can take minutes: keep the lock alive
        ruta = mercado._ruta_velas(par, dia)
        velas = [v for v in mercado.velas_dia(par, dia) if validar_vela(v, 1) and v[5] > 0]
        # the watcher stores the candle in progress and only the next pass replaces it: the file's last candle is
        # trusted only if the file was written after that candle closed (a stop at 15:00:20 leaves 20 s of 15:00)
        if velas and velas[-1][0] + 60 > _mtime(ruta):
            velas.pop()
        if velas:
            total += guardar(par, 1, velas, fuente="vigia")

    def cambio(est):
        e = _entrada(est, par, 1)
        e["dias_vigia"] = sorted(set(e.get("dias_vigia") or []) | set(dias))[-MAX_DIAS_VIGIA:]
    _modificar_estado(cambio)
    avisar(f"Vigía de {mercado.nombre_par(par)}: {len(dias)} días cerrados consolidados ({total} velas)")
    return total


def tramos_pendientes(par, ahora, cfg=None):
    """[(desde_t, hasta_t), ...] the Trades backfill has to cover: holes of the 1-minute history longer than
    hueco_min_minutos inside the last dias_trades days (not already verified as empty) plus the tail up to ahora - 60."""
    return _tramos(par, ahora, configuracion(cfg))


def _tramos(par, ahora, cfg):
    ahora = int(ahora) // 60 * 60
    inicio = ahora - cfg["dias_trades"] * 86400
    if not meses(par, 1):
        return [(inicio, ahora - 60)] if inicio < ahora - 60 else []
    verificados = {tuple(h) for h in (_entrada(_leer_estado(), par, 1).get("huecos_verificados") or []) if isinstance(h, list) and len(h) == 2}
    minimo = cfg["hueco_min_minutos"] * 60
    tramos = []
    velas = cargar(par, inicio, ahora, 1)
    if velas:
        if velas[0][0] - inicio >= minimo:
            tramos.append((inicio, velas[0][0]))
        for h in huecos(velas, 1, minimo):
            tramos.append((h["desde"], h["hasta"]))
        cola = velas[-1][0] + 60
    else:   # no candle inside the window: the tail starts where the history ends, or at the window's start
        r = (resumen().get(par) or {}).get("1") or {}
        cola = max(inicio, min(r.get("hasta_t") or inicio, ahora))
    tramos = [t for t in tramos if t not in verificados and t[0] < t[1]]
    if cola < ahora - 60:
        tramos.append((cola, ahora - 60))
    return tramos


def actualizar(par=None, intervalos=None, dias=None, avisar=print, cfg=None, max_llamadas=None, ahora=None):
    """Per pair: OHLC tail of each interval, consolidation of the watcher's closed days, Trades backfill of the holes
    and the tail. Network errors are caught and reported in 'error'; another process on the pair -> 'ocupado'."""
    cfg = configuracion(cfg)
    pares = mercado.configuracion()["pares"]
    if isinstance(par, str):
        pares = [_par_valido(par)]
    elif par:
        pares = [_par_valido(p) for p in par]
    intervalos = [int(i) for i in (intervalos or cfg["intervalos"]) if int(i) in INTERVALOS] or cfg["intervalos"]
    if dias is not None:
        cfg = {**cfg, "dias_trades": max(0, int(dias))}
    ahora = int(ahora if ahora is not None else time.time()) // 60 * 60
    salida = {}
    presupuesto = max_llamadas
    for par in pares:
        nombre = mercado.nombre_par(par)
        r = {"ohlc": 0, "vigia": 0, "trades": {"velas": 0, "llamadas": 0, "tramos": 0, "terminado": True}, "derivadas": {}, "error": None, "ocupado": False}
        salida[par] = r
        try:
            with ocupar(par):
                for k, n in enumerate(intervalos):
                    if k:
                        mercado._dormir(1.1)
                    latir(par)
                    velas = mercado.descargar(par, n, None)
                    if n == 1:
                        velas = [v for v in velas if v[5] > 0]   # the OHLC API invents volume-0 minutes; the history has none
                    r["ohlc"] += guardar(par, n, velas, fuente="ohlc")
                r["vigia"] = consolidar_vigia(par, avisar)
                tocados = set()
                if cfg["dias_trades"] > 0 or meses(par, 1):
                    tramos = _tramos(par, ahora, cfg)
                    r["trades"]["tramos"] = len(tramos)
                    for i, (a, b) in enumerate(tramos):
                        es_cola = i == len(tramos) - 1 and b == ahora - 60
                        if presupuesto is not None and presupuesto <= 0:
                            r["trades"]["terminado"] = False
                            break
                        avisar(f"Trades {nombre}: {'cola' if es_cola else 'hueco'} {_fecha_hora(a)} → {_fecha_hora(b)}")
                        t = rellenar_con_trades(par, a, b, avisar, max_llamadas=presupuesto, cfg=cfg)
                        if presupuesto is not None:
                            presupuesto -= t["llamadas"]
                        r["trades"]["velas"] += t["velas"]
                        r["trades"]["llamadas"] += t["llamadas"]
                        if t["velas"]:
                            tocados.update(_meses_entre(a, min(b, t["hasta_t"]) + 60))
                        if not t["terminado"]:
                            r["trades"]["terminado"] = False
                            break
                        if t["sin_operaciones"] and not es_cola:
                            def cambio(est, a=a, b=b):
                                e = _entrada(est, par, 1)
                                if [a, b] not in e["huecos_verificados"]:
                                    e["huecos_verificados"] = (e["huecos_verificados"] + [[a, b]])[-500:]
                            _modificar_estado(cambio)
                if tocados:
                    r["derivadas"] = derivar(par, intervalos, sorted(tocados), avisar)
        except Ocupado as err:
            r["ocupado"] = True
            r["error"] = str(err)
            avisar(str(err))
            continue
        except (requests.RequestException, RuntimeError, ValueError) as err:
            r["error"] = f"{type(err).__name__}: {err}" if isinstance(err, requests.RequestException) else str(err)
            avisar(f"{nombre}: {r['error']}")
            continue
        avisar(f"{nombre}: cola OHLC {r['ohlc']} velas ({', '.join(f'{n} min' for n in intervalos)}); vigía {r['vigia']} velas; "
               f"Trades {r['trades']['velas']} velas en {r['trades']['llamadas']} llamadas"
               + ("; derivadas " + ", ".join(f"{d} min {n}" for d, n in r["derivadas"].items()) if r["derivadas"] else "")
               + ("" if r["trades"]["terminado"] else " (sin terminar: relanza para seguir)"))
    return salida


# ---------- coarser intervals derived from the 1-minute candles ----------

def derivar(par, intervalos=None, meses_sel=None, avisar=print):
    """Rebuild the coarser configured intervals (60, 1440…) from the stored 1-minute candles, month by month, so a
    hole the Trades backfill filled at 1 min does not survive in the 60-min series the backtest may read. Only
    months with 1-min candles are touched; inside the data a missing minute is "no trades"; the bucket still open at
    the last 1-min candle is not emitted. Returns {intervalo: velas guardadas}."""
    par = _par_valido(par)
    cfg = configuracion()
    destinos = [int(d) for d in (intervalos or cfg["intervalos"]) if int(d) in INTERVALOS and int(d) > 1]
    todos = meses(par, 1)
    lista = [m for m in todos if not meses_sel or m in set(meses_sel)]
    out = {d: 0 for d in destinos}
    if not lista or not destinos:
        return out
    with ocupar(par):
        ult = leer_mes(par, 1, todos[-1])
        ultimo = ult[-1][0] + 60 if ult else None
        for k, mes in enumerate(lista):
            velas = leer_mes(par, 1, mes)
            if not velas:
                continue
            fin_mes = _t_mes(_mes_siguiente(mes))
            limite = min(fin_mes, ultimo) if ultimo is not None else fin_mes
            for d in destinos:
                derivadas = remuestrear(velas, 1, d, hasta_t=limite)
                if derivadas:
                    out[d] += guardar(par, d, derivadas, fuente="derivado")
            latir(par)
            if (k + 1) % 12 == 0 or k == len(lista) - 1:
                avisar(f"Derivado {mercado.nombre_par(par)} hasta {mes}: " + ", ".join(f"{d} min {out[d]} velas" for d in destinos))
    return out


# ---------- CSV OHLCVT import ----------

def importar_csv(ruta, par=None, intervalo=None, desde=None, hasta=None, avisar=print):
    """Stream a Kraken OHLCVT CSV (<PAR>_<N>.csv, rows timestamp,open,high,low,close,volume[,trades], no header) into the
    history, one month in memory at a time. Idempotent. `desde`/`hasta` are UTC dates (hasta inclusive)."""
    ruta = Path(ruta)
    m = _RE_CSV.match(ruta.stem)
    par = par or (m.group(1) if m else None)
    intervalo = intervalo or (int(m.group(2)) if m else None)
    if not par or not intervalo:
        raise ValueError(f"No puedo deducir el par y el intervalo del nombre {ruta.name}: indícalos con --par y --intervalo")
    par = _par_valido(par)   # 'xbteur' from the CLI would split the manifest key from the files on Windows
    intervalo = int(intervalo)
    desde_t = _t_de_fecha(desde) if desde else None
    hasta_t = _t_de_fecha(hasta) + 86400 if hasta else None
    r = {"par": par, "intervalo": intervalo, "filas": 0, "guardadas": 0, "descartadas": 0, "duplicadas": 0, "desde_t": None, "hasta_t": None}
    buffer, mes_actual, dia_latido = {}, None, None

    def volcar():
        if buffer:
            r["guardadas"] += guardar(par, intervalo, [buffer[t] for t in sorted(buffer)], fuente="csv")
            avisar(f"CSV {mercado.nombre_par(par)} {intervalo} min: {mes_actual} ({len(buffer)} velas)")
            buffer.clear()

    with ocupar(par), open(ruta, newline="", encoding="utf-8", errors="replace") as fh:
        for fila in csv.reader(fh):
            if not fila or not any(c.strip() for c in fila):
                continue
            try:
                t = float(fila[0])
            except ValueError:
                if r["filas"] == 0:
                    continue   # header
                r["filas"] += 1
                r["descartadas"] += 1
                continue
            r["filas"] += 1
            if t > 1e11:
                t = t // 1000   # milliseconds
            v = _normalizar([int(t)] + list(fila[1:6])) if len(fila) >= 6 else None
            if v is None or not validar_vela(v, intervalo):
                r["descartadas"] += 1
                continue
            if (desde_t is not None and v[0] < desde_t) or (hasta_t is not None and v[0] >= hasta_t):
                continue
            mes = mes_de(v[0])
            if mes != mes_actual:
                volcar()
                mes_actual = mes
            if v[0] in buffer:
                r["duplicadas"] += 1
            buffer[v[0]] = v
            r["desde_t"] = v[0] if r["desde_t"] is None else min(r["desde_t"], v[0])
            r["hasta_t"] = v[0] + intervalo * 60 if r["hasta_t"] is None else max(r["hasta_t"], v[0] + intervalo * 60)
            d = v[0] // 86400
            if dia_latido is None or d - dia_latido >= 100:
                dia_latido = d
                latir(par)
        volcar()
    return r


def importar_carpeta(ruta, pares, desde=None, hasta=None, avisar=print):
    """Every <PAR>_<N>.csv of the folder whose PAR is in `pares`, through importar_csv."""
    out = []
    pares = {p.upper() for p in pares}
    for f in sorted(Path(ruta).glob("*.csv")):
        m = _RE_CSV.match(f.stem)
        if m and m.group(1).upper() in pares:
            out.append(importar_csv(f, desde=desde, hasta=hasta, avisar=avisar))
    return out


# ---------- manifest views ----------

def _resumen_de(est):
    out = {}
    for par, intervalos in est.items():
        if not isinstance(intervalos, dict):
            continue
        for clave, e in intervalos.items():
            if not isinstance(e, dict) or not e.get("meses"):
                continue
            try:
                intervalo = int(clave)
            except ValueError:
                continue
            ms = sorted(e["meses"])
            info = [e["meses"][m] for m in ms]
            saltos = sum(len(i.get("huecos") or []) for i in info)
            for a, b in zip(info, info[1:]):
                if b["primero_t"] - a["ultimo_t"] - intervalo * 60 >= 3600:
                    saltos += 1
            desde_t = min(i["primero_t"] for i in info)
            hasta_t = max(i["ultimo_t"] for i in info) + intervalo * 60
            out.setdefault(par, {})[str(intervalo)] = {
                "desde_t": desde_t, "hasta_t": hasta_t, "desde": fecha_utc(desde_t), "hasta": fecha_utc(hasta_t),
                "velas": sum(i["velas"] for i in info), "huecos_largos": saltos,
                "actualizado": e.get("actualizado"), "fuentes": list(e.get("fuentes") or [])}
    return out


def resumen():
    """Per pair and interval (string keys): range, candle count, long holes, last update and sources."""
    if not _ruta_estado().is_file():
        if not _carpeta_hist().is_dir():
            return {}
        return reindexar(lambda _m: None)
    return _resumen_de(_leer_estado())


def reindexar(avisar=print):
    """Rebuild estado.json from the month files (sources, consolidated days and verified holes are kept)."""
    previo = _leer_estado()
    nuevo = {}
    carpeta = _carpeta_hist()
    for ruta_par in sorted(p for p in carpeta.glob("*") if p.is_dir()) if carpeta.is_dir() else []:
        par = ruta_par.name
        for ruta_int in sorted(p for p in ruta_par.glob("*m") if p.is_dir()):
            try:
                intervalo = int(ruta_int.name[:-1])
            except ValueError:
                continue
            if intervalo not in INTERVALOS:
                continue
            e = _entrada(nuevo, par, intervalo)
            viejo = (previo.get(par) or {}).get(str(intervalo)) or {}
            e["fuentes"] = list(viejo.get("fuentes") or [])
            e["dias_vigia"] = list(viejo.get("dias_vigia") or [])
            e["huecos_verificados"] = list(viejo.get("huecos_verificados") or [])
            marca = 0
            for mes in meses(par, intervalo):
                velas = [v for v in leer_mes(par, intervalo, mes) if validar_vela(v, intervalo)]
                if not velas:
                    continue
                velas.sort(key=lambda v: v[0])
                e["meses"][mes] = _resumen_mes(velas, intervalo)
                marca = max(marca, _mtime(ruta_mes(par, intervalo, mes)))
            e["actualizado"] = marca or viejo.get("actualizado")
            if not e["meses"]:
                del nuevo[par][str(intervalo)]
        if par in nuevo and not nuevo[par]:
            del nuevo[par]
    with mercado._lock:
        mercado._escribir_json(_ruta_estado(), nuevo)
    r = _resumen_de(nuevo)
    avisar(f"Manifiesto reconstruido: {sum(len(v) for v in r.values())} series de {len(r)} pares")
    return r


# ---------- validation ----------

def _rel_igual(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-12)


def _vela_igual(a, b):
    """Same candle: OHLC with relative tolerance 1e-9, volume within 1 %."""
    return all(_rel_igual(a[i], b[i], 1e-9) for i in (1, 2, 3, 4)) and _rel_igual(a[5], b[5], 0.01)


def validar(par, avisar=print, ahora=None):
    """Checks the files on disk, the holes, the 1m→60m agreement, the watcher's candles and the freshness. No network."""
    ahora = ahora if ahora is not None else time.time()
    out = {"intervalos": {}, "concordancia_1m_60m_pct": None, "horas_discordantes": [], "discrepancias_vigia": 0, "frescura_min": None}
    for intervalo in INTERVALOS:
        ms = meses(par, intervalo)
        if not ms:
            continue
        r = {"velas": 0, "ficheros_malos": [], "desordenadas": 0, "huecos_largos": [], "minutos_sin_operaciones": 0}
        anterior = None
        for mes in ms:
            velas = leer_mes(par, intervalo, mes)
            malo = False
            for v in velas:
                if not validar_vela(v, intervalo) or mes_de(v[0]) != mes:
                    malo = True
            for a, b in zip(velas, velas[1:]):
                if b[0] <= a[0]:
                    r["desordenadas"] += 1
            if malo:
                r["ficheros_malos"].append(mes)
            r["velas"] += len(velas)
            serie = ([anterior] if anterior else []) + velas
            r["huecos_largos"] += huecos(serie, intervalo, 3600)
            if intervalo == 1:
                for a, b in zip(serie, serie[1:]):
                    salto = b[0] - a[0] - 60
                    if 0 < salto < 3600:
                        r["minutos_sin_operaciones"] += salto // 60
            if velas:
                anterior = velas[-1]
        out["intervalos"][str(intervalo)] = r
        if intervalo == 1 and anterior:
            out["frescura_min"] = max(0, int((ahora - (anterior[0] + 60)) // 60))
        avisar(f"{mercado.nombre_par(par)} {intervalo} min: {r['velas']} velas, {len(r['huecos_largos'])} huecos > 1 h"
               + (f", {len(r['ficheros_malos'])} ficheros con velas inválidas" if r["ficheros_malos"] else "")
               + (f", {r['desordenadas']} desordenadas" if r["desordenadas"] else ""))
    # 1 min resampled to the hour against the native 60 min, month by month, where both exist
    horas_ok = horas_total = 0
    for mes in sorted(set(meses(par, 1)) & set(meses(par, 60))):
        nativas = {v[0]: v for v in leer_mes(par, 60, mes)}
        for h in remuestrear(leer_mes(par, 1, mes), 1, 60):
            n = nativas.get(h[0])
            if n is None:
                continue
            horas_total += 1
            if _vela_igual(h, n):
                horas_ok += 1
            elif len(out["horas_discordantes"]) < 20:
                out["horas_discordantes"].append(h[0])
    if horas_total:
        out["concordancia_1m_60m_pct"] = round(100.0 * horas_ok / horas_total, 2)
    # watcher day files against the 1-minute history (volume-0 candles of the watcher ignored)
    carpeta = mercado._carpeta() / "velas" / par
    if carpeta.is_dir() and meses(par, 1):
        cache = {}
        for ruta in sorted(carpeta.glob("*.json")):
            for v in mercado._leer_json(ruta, []):
                if not validar_vela(v, 1) or v[5] <= 0:
                    continue
                mes = mes_de(v[0])
                if mes not in cache:
                    cache = {mes: {h[0]: h for h in leer_mes(par, 1, mes)}}
                h = cache[mes].get(v[0])
                if h is not None and not _vela_igual(v, h):
                    out["discrepancias_vigia"] += 1
    if out["concordancia_1m_60m_pct"] is not None:
        avisar(f"Concordancia 1 min → 60 min: {out['concordancia_1m_60m_pct']} %")
    if out["discrepancias_vigia"]:
        avisar(f"Velas del vigía distintas del histórico: {out['discrepancias_vigia']}")
    return out


def validar_red(par, avisar=print):
    """Kraken's daily candles of the last 30 days against the 1-minute history resampled to a day (needs network)."""
    ahora = int(time.time())
    hoy = ahora - ahora % 86400
    desde = hoy - 30 * 86400
    diarias = [v for v in mercado.descargar(par, 1440) if desde <= v[0] < hoy]
    mias = {v[0]: v for v in remuestrear(cargar(par, desde, hoy, 1), 1, 1440)}
    r = {"dias": 0, "concordantes": 0, "discordantes": []}
    for d in diarias:
        m = mias.get(d[0])
        if m is None:
            continue
        r["dias"] += 1
        if _rel_igual(d[1], m[1], 1e-9) and _rel_igual(d[4], m[4], 1e-9) and _rel_igual(d[2], m[2], 1e-9) \
                and _rel_igual(d[3], m[3], 1e-9) and _rel_igual(d[5], m[5], 0.01):
            r["concordantes"] += 1
        else:
            r["discordantes"].append(fecha_utc(d[0]))
    avisar(f"{mercado.nombre_par(par)}: {r['concordantes']} de {r['dias']} días coinciden con la diaria de Kraken"
           + (" · discordantes: " + ", ".join(r["discordantes"]) if r["discordantes"] else ""))
    return r


# ---------- inter-process lock ----------

def _ruta_ocupado(par):
    return _carpeta_hist() / par / "ocupado.json"


def bloqueo_vivo(par):
    """The ocupado.json of another process with a fresh heartbeat, or None."""
    datos = mercado._leer_json(_ruta_ocupado(par), None)
    if isinstance(datos, dict) and par not in _propios:
        try:
            if time.time() - float(datos.get("latido") or 0) < LATIDO_MAX:
                return datos
        except (TypeError, ValueError):
            pass
    return None


def bloqueos(pares=None):
    """Pairs whose history another process is updating right now."""
    pares = pares or mercado.configuracion()["pares"]
    return [p for p in pares if bloqueo_vivo(p)]


@contextmanager
def ocupar(par):
    """One writer per pair across processes: refuses (Ocupado) when ocupado.json has a heartbeat younger than
    LATIDO_MAX; reentrant inside this process."""
    if par in _propios:
        yield
        return
    ruta = _ruta_ocupado(par)
    vivo = bloqueo_vivo(par)
    if vivo:
        raise Ocupado(f"Otro proceso está actualizando el histórico de {mercado.nombre_par(par)} (pid {vivo.get('pid')}, "
                      f"desde {_fecha_hora(float(vivo.get('inicio') or vivo.get('latido') or 0))}); espera a que termine o borra {ruta}")
    ahora = time.time()
    _propios[par] = {"ruta": ruta, "inicio": ahora}
    mercado._escribir_json(ruta, {"pid": os.getpid(), "inicio": ahora, "latido": ahora})
    try:
        yield
    finally:
        _propios.pop(par, None)
        try:
            ruta.unlink()
        except OSError:
            pass


def latir(par=None):
    """Refresh the heartbeat of the locks this process holds (every network call, every 100 days of CSV)."""
    for p, info in list(_propios.items()):
        if par is None or p == par:
            try:
                mercado._escribir_json(info["ruta"], {"pid": os.getpid(), "inicio": info["inicio"], "latido": time.time()})
            except OSError:
                pass


# ---------- health check ----------

def _miles(n):
    return f"{int(n):,}".replace(",", ".")


def comprobar(inf, cfg=None):
    """Section «Histórico» of the health check: one item per pair, freshness, and the inter-process lock. Never raises."""
    inf.seccion("Histórico")
    try:
        pares = mercado.configuracion(cfg)["pares"]
        res = resumen()
        ahora = time.time()
        for par in pares:
            nombre = f"{mercado.nombre_par(par)} 1 min"
            r = (res.get(par) or {}).get("1")
            if not r:
                inf.aviso(nombre, "(sin histórico: ejecuta python app.py historico en el PC)")
                continue
            detalle = f"({r['desde']} → {r['hasta']}, {_miles(r['velas'])} velas, {r['huecos_largos']} huecos > 1 h)"
            dias = int((ahora - r["hasta_t"]) // 86400)
            if dias > 2:
                inf.aviso(nombre, f"{detalle} (última vela hace {dias} días)")
            else:
                inf.ok(nombre, detalle)
        for par in bloqueos(pares):
            v = bloqueo_vivo(par) or {}
            inf.aviso("Bloqueo", f"(otro proceso actualiza {mercado.nombre_par(par)}: pid {v.get('pid')}, {_ruta_ocupado(par)})")
    except Exception as err:   # a health check must never take the page down
        inf.aviso("Histórico", f"({err})")
