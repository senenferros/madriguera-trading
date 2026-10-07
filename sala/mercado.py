"""The lookout of the room: one-minute candles from a public exchange API kept on disk, cheap alerts (daily high/low
broken, volume spike), a macro calendar and the owner's simulated-trade journal.

No money, no orders, no API keys: everything here is public data and simulation. The only thing that spends tokens
is the calendar refresh (Claude + web search), and it only runs when the owner orders it.

Files, all under datos/:
    velas/<par>/<día>.json   candles of one pair and local day, [t, open, high, low, close, volume]
    mercado.json             watcher state (last prices, log, what was alerted today)
    alertas.json             alerts that fired
    diario.json              the journal
    calendario_semilla.json  committed seed of macro dates; calendario.json what the archivist adds (not committed)
"""
import json
import math
import os
import re
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

import nucleo

KRAKEN = "https://api.kraken.com/0/public/OHLC"
PARES_POR_DEFECTO = ["XBTEUR", "ETHEUR"]
MAX_ALERTAS = 300          # kept in alertas.json
MAX_DIARIO = 2000          # journal entries kept
MAX_LOG = 40               # lines of the watcher's own log shown in the panel
MAX_CIERRES = 40           # closes kept in the state for the screens of the office
ALERTA_MAX_EDAD = 15 * 60  # a candle that closed earlier than this when first seen is history, not an alert
_lock = threading.RLock()  # every JSON read and write here: the watcher thread and the panel share the files
_paso = threading.Lock()   # one watcher pass at a time in this process

# Patched by the tests: a fake exchange instead of Kraken
descargar_fn = None


# ---------- names, files ----------

def nombre_par(par):
    """Kraken pair code -> what people call it ('XBTEUR' -> 'BTC/EUR')."""
    base, cotiza = par[:-3], par[-3:]
    base = {"XBT": "BTC"}.get(base, base)
    return f"{base}/{cotiza}"


def simbolo(par):
    return {"EUR": "€", "USD": "$", "GBP": "£"}.get(par[-3:], par[-3:])


def _carpeta():
    return nucleo.DATOS_DIR


def _leer_json(ruta, defecto):
    # On Windows a reader gets PermissionError while another process is replacing the file: retry briefly.
    for intento in range(20):
        try:
            with _lock:
                return json.loads(ruta.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return defecto
        except PermissionError:
            time.sleep(0.05 * (intento + 1))
        except (OSError, ValueError):
            return defecto
    return defecto


def _escribir_json(ruta, datos):
    """Atomic write: a temp file unique to this process and thread, then a rename, retried while a reader holds the file."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.parent / f"{ruta.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _lock:
        tmp.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
        for intento in range(40):
            try:
                tmp.replace(ruta)
                return
            except PermissionError:   # on Windows a reader (or the antivirus) keeps the file open for a moment
                time.sleep(0.05 * (intento + 1) if intento < 10 else 0.5)
        try:
            tmp.replace(ruta)
        finally:
            tmp.unlink(missing_ok=True)


def estado():
    """Watcher state: last prices, when it last managed to download, which alerts went out today."""
    return _leer_json(_carpeta() / "mercado.json", {})


def _guardar_estado(datos):
    _escribir_json(_carpeta() / "mercado.json", datos)


def _hora(valor, defecto="08:00"):
    """'8:00', '08:00' or the base-60 int YAML makes of an unquoted 8:00 -> 'HH:MM', so it compares as text."""
    if isinstance(valor, int) and not isinstance(valor, bool):
        valor = "%02d:%02d" % divmod(valor, 60)
    try:
        h, m = str(valor).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return "%02d:%02d" % (h, m)
    except ValueError:
        pass
    return defecto


def configuracion(cfg=None):
    """The room's settings from config.yaml, validated with defaults for anything missing or odd."""
    cfg = cfg if cfg is not None else nucleo.cargar_config()
    alertas = cfg.get("alertas") or {}
    return {
        "pares": [str(p) for p in (cfg.get("pares") or PARES_POR_DEFECTO) if re.fullmatch(r"[A-Z0-9]{6,12}", str(p))] or list(PARES_POR_DEFECTO),
        "intervalo": int(cfg.get("intervalo_minutos") or 1),
        "volumen_x": float(alertas.get("volumen_x") or 3),
        "enfriamiento": int(alertas.get("enfriamiento_minutos") or 30) * 60,
        "minimo_velas": int(alertas.get("minimo_velas_dia") or 30),
        "hora_parte": _hora(cfg.get("hora_parte") or "08:00"),
        "conservar_dias": max(0, int(cfg.get("conservar_dias") if cfg.get("conservar_dias") is not None else 400)),   # 0 = never prune
    }


# ---------- candles on disk: one file per pair and local day, [t, open, high, low, close, volume] ----------

def _ruta_velas(par, dia):
    return _carpeta() / "velas" / par / f"{dia}.json"


def velas_dia(par, dia=None):
    dia = dia or date.today().isoformat()
    return _leer_json(_ruta_velas(par, dia), [])


def dia_local(ts):
    # Windows refuses fromtimestamp for negative timestamps (pre-1970 history): fall back to UTC there
    try:
        return datetime.fromtimestamp(ts).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)).date().isoformat()


def guardar_velas(par, nuevas):
    """Merge candles into their day files (deduplicated by timestamp, the in-progress candle replaced)."""
    por_dia = {}
    for v in nuevas:
        por_dia.setdefault(dia_local(v[0]), []).append(v)
    with _lock:
        for dia, lista in por_dia.items():
            ruta = _ruta_velas(par, dia)
            actuales = {v[0]: v for v in _leer_json(ruta, [])}
            actuales.update({v[0]: v for v in lista})
            _escribir_json(ruta, [actuales[t] for t in sorted(actuales)])


def podar_velas(par, conservar_dias):
    """Delete day files older than the retention set in config.yaml (default about 13 months, kept for backtesting)."""
    if conservar_dias <= 0:   # 0 = never prune
        return 0
    limite = (date.today() - timedelta(days=conservar_dias)).isoformat()
    carpeta = _carpeta() / "velas" / par
    borrados = 0
    for ruta in (carpeta.glob("*.json") if carpeta.is_dir() else []):
        if ruta.stem < limite:
            try:
                ruta.unlink()
                borrados += 1
            except OSError:
                pass
    return borrados


def descargar(par, intervalo=1, desde=None):
    """One-minute candles from Kraken's public API (no key). Raises requests.RequestException or RuntimeError."""
    if descargar_fn is not None:
        return descargar_fn(par, intervalo, desde)
    params = {"pair": par, "interval": intervalo}
    if desde:
        params["since"] = int(desde)
    r = requests.get(KRAKEN, params=params, timeout=nucleo.TIMEOUT)
    if not r.ok:
        raise RuntimeError(f"Kraken: HTTP {r.status_code} {r.reason}")
    datos = r.json()
    if datos.get("error"):
        raise RuntimeError("Kraken: " + "; ".join(datos["error"]))
    resultado = datos.get("result") or {}
    clave = next((k for k in resultado if k != "last"), None)
    if not clave:
        raise RuntimeError(f"Kraken: sin datos para {par}")
    velas = []
    for fila in resultado[clave]:
        try:
            velas.append([int(fila[0]), float(fila[1]), float(fila[2]), float(fila[3]), float(fila[4]), float(fila[6])])
        except (TypeError, ValueError, IndexError):
            continue
    return velas


# ---------- alerts: pure functions over candle lists, so they can be tested without network ----------

def completadas(velas, ahora=None, intervalo=1):
    ahora = ahora or time.time()
    return [v for v in velas if v[0] + intervalo * 60 <= ahora]


def rotura_dia(velas_dia_cerradas, ultima, minimo=30, manana=None):
    """'max' or 'min' when the candle closes beyond the day's previous range (after enough candles), else None.

    `manana` is the summary of the part of the day the one-minute file does not cover (see _rellenar_manana).
    """
    previas = [v for v in velas_dia_cerradas if v[0] < ultima[0]]
    manana = manana or {}
    if len(previas) + manana.get("minutos", 0) < minimo:
        return None
    maximos = [v[2] for v in previas] + ([manana["maximo"]] if manana.get("maximo") is not None else [])
    minimos = [v[3] for v in previas] + ([manana["minimo"]] if manana.get("minimo") is not None else [])
    if maximos and ultima[4] > max(maximos):
        return "max"
    if minimos and ultima[4] < min(minimos):
        return "min"
    return None


def pico_volumen(velas_cerradas, factor=3.0, ventana=60):
    """The last closed candle's volume against the median of the previous `ventana` candles; the ratio or None."""
    if len(velas_cerradas) < ventana + 1:
        return None
    ultima, previas = velas_cerradas[-1], velas_cerradas[-ventana - 1:-1]
    mediana = statistics.median(v[5] for v in previas)
    if mediana <= 0 or ultima[5] < factor * mediana:
        return None
    return round(ultima[5] / mediana, 1)


def fmt_precio(x, par="XBTEUR"):
    """Spanish number format: 58.230,5 €."""
    if x is None:
        return "—"
    dec = 0 if x >= 1000 else 2
    s = f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {simbolo(par)}"


# ---------- one watcher pass ----------

def _inicio_dia(ahora):
    d = datetime.fromtimestamp(ahora)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _rellenar_manana(p, par, velas, hoy, inicio, intervalo):
    """Kraken only serves the last 720 candles, so after a first start at noon or a long sleep today's one-minute file
    begins mid-day. Fetch the missing morning once as 5-minute candles and keep its open/high/low/volume in the state,
    so the day's open, range and breakouts are the real day's and not the file's."""
    manana = p.get("manana") or {}
    if not velas or (manana.get("dia") == hoy and manana.get("listo")):
        return
    if velas[0][0] <= inicio + intervalo * 60:
        p["manana"] = {"dia": hoy, "listo": True}   # the file starts with the day's first candle: nothing missing
        return
    _dormir(1.1)
    previas = [v for v in descargar(par, max(5, intervalo), inicio - 300) if inicio <= v[0] < velas[0][0]]
    p["manana"] = {"dia": hoy, "listo": True}
    if previas:
        p["manana"].update({"apertura": previas[0][1], "maximo": max(v[2] for v in previas), "minimo": min(v[3] for v in previas),
                            "volumen": round(sum(v[5] for v in previas), 3), "minutos": max(5, intervalo) * len(previas)})


def _dormir(segundos):
    """Kraken asks for a second between public calls; a fake exchange (tests) does not need the wait."""
    if descargar_fn is None:
        time.sleep(segundos)


def vigilar(avisar=print, ahora=None, cfg=None):
    """Download the latest candles of every pair, store them, and return the alerts that fired ([] when quiet).

    Network errors never raise: the state file records 'sin_conexion' and the last good prices stay on screen.
    One pass at a time per process; the loop in the panel is the only caller while the panel runs.
    """
    with _paso:
        return _vigilar(avisar, ahora, configuracion(cfg))


def _vigilar(avisar, ahora, cfg):
    ahora = ahora or time.time()
    hoy = dia_local(ahora)
    inicio = _inicio_dia(ahora)
    est = estado()
    for clave in ("pares", "avisado", "enfriamiento"):   # pairs removed from config.yaml leave the screens too
        est[clave] = {k: v for k, v in (est.get(clave) or {}).items() if k in cfg["pares"]}
    alertas = []
    errores = []
    avisos = []
    for i, par in enumerate(cfg["pares"]):
        if i:
            _dormir(1.1)
        p = est["pares"].setdefault(par, {})
        try:
            desde = (p.get("ultima_t") or 0) - 180
            nuevas = descargar(par, cfg["intervalo"], desde if desde > 0 else None)
        except (requests.RequestException, RuntimeError, ValueError) as err:
            errores.append(f"{nombre_par(par)}: {type(err).__name__ if isinstance(err, requests.RequestException) else err}")
            continue
        if not nuevas:
            continue
        guardar_velas(par, nuevas)
        velas = velas_dia(par, hoy)
        try:
            _rellenar_manana(p, par, velas, hoy, inicio, cfg["intervalo"])
        except (requests.RequestException, RuntimeError, ValueError) as err:   # tried again next pass
            avisos.append(f"{nombre_par(par)}: sin la mañana ({type(err).__name__ if isinstance(err, requests.RequestException) else err})")
        manana = p.get("manana") if (p.get("manana") or {}).get("dia") == hoy else {}
        parcial = bool(velas) and velas[0][0] > inicio + cfg["intervalo"] * 60 and manana.get("apertura") is None
        cerradas = completadas(velas, ahora, cfg["intervalo"])
        ultima = nuevas[-1]
        maximos = [v[2] for v in velas] + ([manana["maximo"]] if manana.get("maximo") is not None else [])
        minimos = [v[3] for v in velas] + ([manana["minimo"]] if manana.get("minimo") is not None else [])
        p.update({"precio": ultima[4], "ultima_t": ultima[0], "hora": time.strftime("%H:%M", time.localtime(ultima[0])),
                  "apertura": manana["apertura"] if manana.get("apertura") is not None else (velas[0][1] if velas else None),
                  "maximo": max(maximos) if maximos else None,
                  "minimo": min(minimos) if minimos else None,
                  "volumen": round(sum(v[5] for v in velas) + manana.get("volumen", 0), 3) if velas else None,
                  "desde": time.strftime("%H:%M", time.localtime(velas[0][0])) if parcial else None,
                  "cierres": [round(v[4], 2) for v in velas[-MAX_CIERRES:]]})
        p["cambio_dia"] = round(100.0 * (ultima[4] - p["apertura"]) / p["apertura"], 2) if p.get("apertura") else None
        # alerts: every candle closed since the last pass is looked at (a pass takes a bit more than a minute, so now
        # and then two close in between), each side at most once a day, volume with a cooldown
        avisado = est["avisado"].setdefault(par, {})
        if avisado.get("dia") != hoy:
            avisado.clear()
            avisado["dia"] = hoy
        evaluada = p.get("evaluada_t")
        pendientes = [v for v in cerradas if v[0] > evaluada] if evaluada else cerradas[-1:]
        for v in pendientes:
            if v[0] < ahora - ALERTA_MAX_EDAD:
                continue   # closed hours ago (the PC was asleep): history, not an alert
            hasta = [c for c in cerradas if c[0] <= v[0]]
            lado = rotura_dia(hasta, v, cfg["minimo_velas"], manana)
            if lado and not avisado.get(lado):
                avisado[lado] = True
                alertas.append({"par": par, "tipo": "rotura_" + lado, "precio": v[4],
                                "texto": f"{'📈' if lado == 'max' else '📉'} {nombre_par(par)} rompe el {'máximo' if lado == 'max' else 'mínimo'} del día: {fmt_precio(v[4], par)}"})
            ratio = pico_volumen(hasta, cfg["volumen_x"])
            if ratio and ahora - est["enfriamiento"].get(par, 0) > cfg["enfriamiento"]:
                est["enfriamiento"][par] = ahora
                alertas.append({"par": par, "tipo": "volumen", "precio": v[4],
                                "texto": f"🔊 {nombre_par(par)}: volumen x{ratio} sobre lo normal en el último minuto, a {fmt_precio(v[4], par)}"})
        if cerradas:
            p["evaluada_t"] = cerradas[-1][0]
    if est.get("podado") != hoy:   # once a day, drop candle files older than the retention
        for par in cfg["pares"]:
            podar_velas(par, cfg["conservar_dias"])
        est["podado"] = hoy
    est["sin_conexion"] = bool(errores) and len(errores) == len(cfg["pares"])
    est["error"] = "; ".join(errores)
    if not est["sin_conexion"]:
        est["actualizado"] = ahora
    linea = " · ".join(f"{nombre_par(par)} {fmt_precio(p.get('precio'), par)}"
                       + (f" ({p['cambio_dia']:+.2f} %)" if p.get("cambio_dia") is not None else "")
                       for par, p in est["pares"].items() if p.get("precio"))
    est["ultimo"] = linea or ("Sin conexión con el exchange" if errores else "Esperando datos…")
    log = est.get("log") or []
    log.append(time.strftime("%H:%M:%S  ", time.localtime(ahora)) + (("Sin conexión: " + est["error"]) if errores else est["ultimo"])
               + ((" · " + "; ".join(avisos)) if avisos else ""))
    est["log"] = log[-MAX_LOG:]
    _guardar_estado(est)
    if alertas:
        anotar_alertas(alertas, ahora)
        for a in alertas:
            avisar("Vigía: " + a["texto"])
    elif errores:
        avisar("Vigía: sin conexión con el exchange (" + est["error"] + ")")
    return alertas


def toca_parte(ahora=None, cfg=None):
    """True from the hour set in config.yaml until anotar_parte_enviado() marks the day (so a failed send is retried)."""
    ahora = ahora or time.time()
    return (estado().get("parte_enviado") != dia_local(ahora)
            and time.strftime("%H:%M", time.localtime(ahora)) >= configuracion(cfg)["hora_parte"])


def anotar_parte_enviado(dia=None):
    with _paso, _lock:
        est = estado()
        est["parte_enviado"] = dia or date.today().isoformat()
        _guardar_estado(est)


def anotar_alertas(alertas, ahora=None):
    ahora = ahora or time.time()
    ruta = _carpeta() / "alertas.json"
    with _lock:
        lista = _leer_json(ruta, [])
        lista += [{**a, "ts": ahora, "fecha": time.strftime("%d/%m %H:%M", time.localtime(ahora))} for a in alertas]
        _escribir_json(ruta, lista[-MAX_ALERTAS:])


def alertas(n=50):
    return list(reversed(_leer_json(_carpeta() / "alertas.json", [])))[:n]


# ---------- macro calendar (committed seed, refreshed by Claude on demand) ----------

ESQUEMA_CALENDARIO = {
    "type": "object",
    "properties": {
        "eventos": {"type": "array", "maxItems": 60, "items": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "AAAA-MM-DD"},
                "hora": {"type": "string", "description": "Hora de Madrid HH:MM, o vacío si no se sabe"},
                "evento": {"type": "string"},
                "zona": {"type": "string", "enum": ["EEUU", "Eurozona", "España", "Cripto", "Otro"]},
                "importancia": {"type": "string", "enum": ["alta", "media", "baja"]},
                "fuente": {"type": "string", "description": "URL donde se ha comprobado la fecha"},
            },
            "required": ["fecha", "hora", "evento", "zona", "importancia", "fuente"]}},
        "notas": {"type": "string"},
    },
    "required": ["eventos", "notas"],
}


ZONAS = ("EEUU", "Eurozona", "España", "Cripto", "Otro")
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_HORA = re.compile(r"^(\d{1,2}:\d{2})?$")


def evento_valido(e):
    """Only well-formed events are stored or shown: ISO date, HH:MM or empty hour, an http(s) source or none.
    The entries come from Claude's web search (or a hand-edited file), so nothing here is trusted as is."""
    if not isinstance(e, dict) or not isinstance(e.get("fecha"), str) or not _RE_FECHA.match(e["fecha"]):
        return None
    try:
        date.fromisoformat(e["fecha"])
    except ValueError:
        return None
    hora = str(e.get("hora") or "").strip()
    fuente = str(e.get("fuente") or "").strip()
    if urlparse(fuente).scheme not in ("http", "https"):
        fuente = ""
    evento = " ".join(str(e.get("evento") or "").split())[:200]
    if not evento:
        return None
    return {"fecha": e["fecha"], "hora": hora if _RE_HORA.match(hora) else "", "evento": evento,
            "zona": e.get("zona") if e.get("zona") in ZONAS else "Otro",
            "importancia": e.get("importancia") if e.get("importancia") in ("alta", "media", "baja") else "media",
            "fuente": fuente[:500]}


def calendario():
    """The committed seed (calendario_semilla.json) plus what the archivist has added (calendario.json, not in git)."""
    carpeta = _carpeta()
    semilla = _leer_json(carpeta / "calendario_semilla.json", {})
    datos = _leer_json(carpeta / "calendario.json", {})
    eventos, claves = [], set()
    for e in list(datos.get("eventos") or []) + list(semilla.get("eventos") or []):
        e = evento_valido(e)
        if not e or (e["fecha"], e["evento"].lower()) in claves:
            continue
        claves.add((e["fecha"], e["evento"].lower()))
        eventos.append(e)
    eventos.sort(key=lambda e: (e["fecha"], e["hora"]))
    return {"eventos": eventos, "actualizado": datos.get("actualizado"), "notas": datos.get("notas") or semilla.get("notas", "")}


def proximos(dias=14, desde=None):
    hoy = (desde or date.today())
    fin = (hoy + timedelta(days=dias)).isoformat()
    return [e for e in calendario()["eventos"] if hoy.isoformat() <= e["fecha"] <= fin]


def actualizar_calendario(avisar=print, preguntar=None):
    """Ask Claude (with web search) for the coming weeks' macro dates; manual entries are kept, repeats merged.

    `preguntar(prompt, schema, herramientas, timeout)` defaults to sala.claude.preguntar; the tests pass a fake.
    Without the `claude` CLI the log says so and nothing changes.
    """
    if preguntar is None:
        from sala import claude
        if not claude.disponible():
            avisar("Documentalista: no encuentro Claude Code (comando «claude») en el PATH; el calendario se queda como está.")
            return []
        preguntar = claude.preguntar
    actual = calendario()
    hoy = date.today().isoformat()
    venideros = [e for e in actual["eventos"] if e["fecha"] >= hoy][:60]
    prompt = (
        "Eres el documentalista de datos de la sala de La Madriguera Trading. Busca en la web las fechas "
        f"confirmadas de los próximos 45 días desde {hoy} para: reuniones y decisiones de tipos de la Reserva Federal (FOMC) "
        "y del BCE, publicación del IPC de EEUU y de la Eurozona, nóminas no agrícolas de EEUU, PIB de EEUU y de la Eurozona, "
        "vencimientos trimestrales de opciones y futuros (incluidos los de Bitcoin en Deribit y CME), y grandes "
        "actualizaciones o eventos de Bitcoin y Ethereum. Comprueba cada fecha en una fuente oficial o de primer nivel "
        "y pon la URL en «fuente». Hora en hora de Madrid. No inventes fechas: si no la encuentras confirmada, no la pongas.\n"
        f"Ya tenemos estos eventos (no los repitas, corrígelos si están mal): {json.dumps(venideros, ensure_ascii=False)}"
    )
    avisar("Documentalista: buscando fechas macro confirmadas…")
    datos, coste = preguntar(prompt, ESQUEMA_CALENDARIO, ["WebSearch", "WebFetch"], timeout=900)
    claves = {(e["fecha"], e["evento"].lower()) for e in actual["eventos"]}
    nuevos = []
    for e in (datos or {}).get("eventos") or []:
        e = evento_valido(e)
        if e and e["fecha"] >= hoy and (e["fecha"], e["evento"].lower()) not in claves:
            claves.add((e["fecha"], e["evento"].lower()))
            nuevos.append(e)
    limite = (date.today() - timedelta(days=30)).isoformat()   # a month of past dates stays, for the record
    todos = [e for e in actual["eventos"] + nuevos if e["fecha"] >= limite]
    todos.sort(key=lambda e: (e["fecha"], e["hora"]))
    _escribir_json(_carpeta() / "calendario.json",
                   {"eventos": todos, "actualizado": time.time(), "notas": str((datos or {}).get("notas") or "")[:2000], "coste_usd": coste})
    avisar(f"Documentalista: {len(nuevos)} fechas nuevas en el calendario ({len(todos)} en total).")
    return nuevos


# ---------- the owner's journal (simulation notes; nothing here places an order) ----------

TIPOS_DIARIO = {"nota": "Nota", "compra_sim": "Compra (simulada)", "venta_sim": "Venta (simulada)", "idea": "Idea de estrategia"}


def numero(texto, campo="Número"):
    """'58.230,5', '58230.5', '0,01' or '0.01' -> float; a dot is a thousands separator only next to a comma or in 1.234.567.
    Empty -> None. Raises ValueError with a message for the form."""
    s = (texto or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s or re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"{campo} no válido: escribe un número como 58.230,5 o 0,01.")
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{campo} no válido: tiene que ser un número mayor que cero.")
    return v


def diario(n=200):
    datos = _leer_json(_carpeta() / "diario.json", {"lista": []})
    return list(reversed(datos.get("lista", [])))[:n]


def anotar_diario(tipo, texto, par="", precio=None, cantidad=None, autor="jefe"):
    if tipo not in TIPOS_DIARIO:
        raise ValueError("tipo no válido")
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Escribe algo.")
    for nombre, valor in (("Precio", precio), ("Cantidad", cantidad)):
        if valor is not None and (isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor) or valor <= 0):
            raise ValueError(f"{nombre} no válido: tiene que ser un número mayor que cero.")
    entrada = {"ts": time.time(), "fecha": time.strftime("%d/%m/%Y %H:%M"), "tipo": tipo, "texto": texto[:2000],
               "par": par[:12] if par else "", "precio": precio, "cantidad": cantidad, "autor": autor}
    ruta = _carpeta() / "diario.json"
    with _lock:
        datos = _leer_json(ruta, {"lista": []})
        datos["lista"] = (datos.get("lista") or [])[-MAX_DIARIO + 1:] + [entrada]
        _escribir_json(ruta, datos)
    return entrada


# ---------- what the panel and the office read ----------

def resumen(cfg=None):
    """Everything the markets page shows: prices, day range, last closes for a sparkline, alerts, calendar, journal."""
    cfg = configuracion(cfg)
    est = estado()
    hoy = date.today().isoformat()
    pares = []
    for par in cfg["pares"]:
        p = {"precio": None, "cambio_dia": None, "apertura": None, "maximo": None, "minimo": None, "volumen": None, "hora": None,
             "ultima_t": None, "desde": None}
        p.update(est.get("pares", {}).get(par) or {})   # before the first download every field is simply None
        velas = velas_dia(par, hoy)
        cierres = [round(v[4], 2) for v in velas[-240:]]
        pares.append({"par": par, "nombre": nombre_par(par), "simbolo": simbolo(par), "velas_hoy": len(velas),
                      "cierres": cierres[::2] if len(cierres) > 120 else cierres, **p,
                      "texto": fmt_precio(p.get("precio"), par) if p.get("precio") is not None else "",
                      "maximo_txt": fmt_precio(p.get("maximo"), par), "minimo_txt": fmt_precio(p.get("minimo"), par)})
    return {"pares": pares, "actualizado": est.get("actualizado"), "sin_conexion": est.get("sin_conexion", False),
            "error": est.get("error", ""), "ultimo": est.get("ultimo", ""), "log": est.get("log", [])[-12:],
            "alertas": alertas(40), "calendario": proximos(21), "diario": diario(100),
            "hora_parte": cfg["hora_parte"], "tipos_diario": TIPOS_DIARIO, "parte_enviado": est.get("parte_enviado")}


def resumen_corto():
    """For the office screens (polled often, so it only reads one small file)."""
    est = estado()
    return {"pares": [{"par": par, "nombre": nombre_par(par), "precio": p.get("precio"), "texto": fmt_precio(p.get("precio"), par),
                       "cambio_dia": p.get("cambio_dia"), "hora": p.get("hora"), "cierres": p.get("cierres") or []}
                      for par, p in est.get("pares", {}).items()],
            "alerta_ultima": (alertas(1) or [{}])[0].get("texto", ""),
            "sin_conexion": est.get("sin_conexion", False), "actualizado": est.get("actualizado"), "ultimo": est.get("ultimo", "")}


def parte(cfg=None):
    """The morning report: prices, day range, yesterday's alerts and what the calendar has for today and tomorrow."""
    r = resumen(cfg)
    hoy = date.today()
    lineas = ["📊 Parte de La Madriguera Trading · " + hoy.strftime("%d/%m/%Y")]
    for p in r["pares"]:
        if p.get("precio") is None:
            continue
        lineas.append(f"{p['nombre']}: {fmt_precio(p['precio'], p['par'])}"
                      + (f" ({p['cambio_dia']:+.2f} % hoy)" if p.get("cambio_dia") is not None else "")
                      + (f" · rango {fmt_precio(p['minimo'], p['par'])} – {fmt_precio(p['maximo'], p['par'])}" if p.get("maximo") else "")
                      + (f" (desde las {p['desde']})" if p.get("desde") else ""))
    ayer = time.time() - 86400
    recientes = [a for a in r["alertas"] if a.get("ts", 0) > ayer]
    lineas.append(f"Alertas en 24 h: {len(recientes)}" + (" · última: " + recientes[0]["texto"] if recientes else ""))
    agenda = [e for e in r["calendario"] if e["fecha"] in (hoy.isoformat(), (hoy + timedelta(days=1)).isoformat())]
    if agenda:
        lineas.append("Agenda: " + "; ".join(f"{e['fecha'][8:]}/{e['fecha'][5:7]} {e.get('hora') or ''} {e['evento']}".strip() for e in agenda))
    if r["sin_conexion"]:
        lineas.append("⚠️ Sin conexión con el exchange: los precios son los últimos que se pudieron leer.")
    lineas.append("Todo es simulación y datos públicos: aquí no hay dinero real.")
    return "\n".join(lineas)


def comprobar(inf, cfg=None):
    """Health check: can this PC reach the exchange, do we have candles today, is the calendar there?"""
    cfg = configuracion(cfg)
    inf.seccion("Exchange y datos")
    try:
        velas = descargar(cfg["pares"][0], cfg["intervalo"])
        inf.ok("Kraken (datos públicos)", f"({nombre_par(cfg['pares'][0])} {fmt_precio(velas[-1][4], cfg['pares'][0])})")
    except (requests.RequestException, RuntimeError, ValueError, IndexError) as err:
        inf.error("Kraken", f"(sin conexión: {type(err).__name__})")
    est = estado()
    if est.get("actualizado"):
        inf.ok("Vigía", f"(última lectura {time.strftime('%d/%m %H:%M', time.localtime(est['actualizado']))})")
    else:
        inf.aviso("Vigía", "(todavía no ha leído precios: arranca el panel o ejecuta «python app.py vigilar»)")
    hoy = date.today().isoformat()
    for par in cfg["pares"]:
        n = len(velas_dia(par, hoy))
        inf.ok(f"Velas de hoy {nombre_par(par)}", f"({n})") if n else inf.aviso(f"Velas de hoy {nombre_par(par)}", "(ninguna todavía)")
    n = len(calendario()["eventos"])
    inf.ok("Calendario macro", f"({n} fechas)") if n else inf.aviso("Calendario macro", "(vacío)")
