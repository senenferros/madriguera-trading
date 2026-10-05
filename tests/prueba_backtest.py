"""Pruebas de la Fase 1 sin red: histórico de Kraken (disco, lector, fuentes falsas), motor de backtest, métricas,
walk-forward, estrategias, veredicto, página /backtest y CLI. Determinista (día UTC, semillas fijas).

    python tests/prueba_backtest.py

Imprime una línea por comprobación y «Todo bien.» al final; con fallos, «N fallos» y código de salida 1.
Objetivo: el fichero entero en menos de 90 s.
"""
import contextlib
import csv
import io
import json
import math
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-bt-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

import requests  # noqa: E402

from sala import equipo, mercado  # noqa: E402
import panel  # noqa: E402

# The Fase 1 modules are imported one by one: a missing one fails its sections, not the whole harness
FALTAN = []
try:
    from sala import historico
except ImportError as _e:
    historico, FALTAN = None, FALTAN + [f"sala.historico ({_e})"]
try:
    from sala import estrategias
except ImportError as _e:
    estrategias, FALTAN = None, FALTAN + [f"sala.estrategias ({_e})"]
try:
    from sala import backtest
except ImportError as _e:
    backtest, FALTAN = None, FALTAN + [f"sala.backtest ({_e})"]

INICIO_PRUEBAS = time.time()
fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


def cerca(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def seccion(titulo, fn, *modulos):
    """Run one section; an exception inside it is one failure, the rest of the harness goes on."""
    print(titulo)
    faltan = [n for n, m in modulos if m is None]
    if faltan:
        ok(False, f"{titulo}: faltan los módulos {', '.join(faltan)}")
        return
    try:
        fn()
    except Exception as e:   # noqa: BLE001 - the harness must report and continue
        ok(False, f"{titulo}: excepción {type(e).__name__}: {e}")
        traceback.print_exc()


# ---------- fakes and generators (§9.0) ----------

AHORA = int(time.time()) // 60 * 60
T_TR = 1_700_000_100                      # multiple of 300
T0 = 1_704_067_200                        # 2024-01-01 00:00 UTC
N_TRADES_FALSOS = 30_000


def exchange_historico(par, intervalo=1, desde=None):
    """2000 synthetic candles of `intervalo` minutes ending at the current bucket (in progress), capped at 720 like Kraken."""
    paso = intervalo * 60
    fin = AHORA - AHORA % paso
    velas = []
    for k in range(2000):
        t = fin - (1999 - k) * paso
        p = 60000 + 10 * math.sin(k / 50)
        vol = 0.0 if (intervalo == 1 and k % 10 == 0) else 1.0
        velas.append([t, p, p + 5, p - 5, p + 1, vol])
    return [v for v in velas if desde is None or v[0] >= desde][-720:]


def trades_falsos(par, desde_ns):
    """Trades every 3 s from T_TR; the first 1000 with t*1e9 >= desde_ns (inclusive, Kraken repeats the cursor trade)."""
    ns3 = 3 * 10 ** 9
    k0 = -(-(int(desde_ns) - T_TR * 10 ** 9) // ns3)   # ceil
    k0 = max(0, k0)
    filas = []
    for k in range(k0, min(k0 + 1000, N_TRADES_FALSOS)):
        t = T_TR + 3 * k
        filas.append([f"{100 + ((k * 7) % 13) / 10:.1f}", "0.1", float(t), "b", "m", "", k])
    if not filas:
        return [], str(int(desde_ns))
    return filas, str((T_TR + 3 * (k0 + len(filas) - 1)) * 10 ** 9)


def serie_sintetica(dias, marco=15, semilla=0, patron="plana", t0=T0):
    """Synthetic candles: 'plana' (random walk), 'rotura' (one afternoon breakout a day, 20 % of them false), 'constante'."""
    rng = random.Random(semilla)
    por_dia = 1440 // marco
    c_prev = 100.0
    out = []

    def base_normal(j):
        if j < 48:
            return 100 + 0.25 * math.sin(2 * math.pi * j / 16)
        if j < 72:
            return 100.5 + 3.0 * (j - 48) / 24
        if j < 89:
            return 103.5
        return 103.5 - 3.5 * (j - 88) / 7

    def base_falso(j):
        if j < 48:
            return 100 + 0.25 * math.sin(2 * math.pi * j / 16)
        if j < 52:
            return 100.5 + 0.8 * (j - 48) / 4
        if j < 60:
            return 101.3 - 2.8 * (j - 52) / 8
        if j < 88:
            return 98.5 + 1.5 * (j - 60) / 28
        return 100.0

    for d in range(dias):
        falso = rng.random() < 0.2 if patron == "rotura" else False
        for j in range(por_dia):
            t = t0 + d * 86400 + j * marco * 60
            vol = 1 + rng.random() * 0.2
            if patron == "plana":
                c = c_prev * (1 + rng.gauss(0, 0.002))
                o = c_prev
                h = max(o, c) * (1 + abs(rng.gauss(0, 0.0007)))
                l = min(o, c) * (1 - abs(rng.gauss(0, 0.0007)))
            elif patron == "rotura":
                # Wicks of 0.1 (not 0.3): the afternoon climb of 0.125 a bar must clear the previous bar's high, so the
                # breakout happens at 12:30-12:45 UTC and not at a noise-driven bar. The first bar of the day carries an
                # "overnight" high of 100.6, so the morning (sine +-0.25) never breaks the day's range on its own.
                c = (base_falso(j) if falso else base_normal(j)) + rng.gauss(0, 0.08)
                o = c_prev
                h = max(o, c) + 0.1
                l = min(o, c) - 0.1
                if j == 0:
                    h = max(h, 100.6)
            elif patron == "constante":
                o = h = l = c = 100.0
            else:
                raise ValueError(patron)
            out.append([t, round(o, 6), round(h, 6), round(l, 6), round(c, 6), round(vol, 6)])
            c_prev = c
    return out


def sembrar(par, marco, velas):
    return historico.guardar(par, marco, velas, fuente="test")


def limpiar_historico():
    shutil.rmtree(nucleo.DATOS_DIR / "historico", ignore_errors=True)


def cfg_hist(**kw):
    """Validated historico config that also carries its raw section, so it works whether the callee re-validates or not."""
    bruto = {"historico": dict(kw)}
    out = historico.configuracion(bruto)
    out["historico"] = bruto["historico"]
    return out


def cfg_bt(**kw):
    bruto = {"backtest": {"dia": "utc", "semillas_azar": 40, **kw}}
    out = backtest.configuracion(bruto)
    out["backtest"] = bruto["backtest"]
    return out


CFG = cfg_bt() if backtest is not None else None
CORRER_N = 0   # how many times backtest.correr has run (pruebas_total must match)

if historico is not None:
    historico.descargar_trades_fn = trades_falsos
mercado.descargar_fn = exchange_historico


def dia_utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def hora_utc(t):
    return (t % 86400) // 3600


def listado(carpeta):
    """(relative path, size) of every file under a folder: to prove that nothing changed."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        return []
    return sorted((str(p.relative_to(carpeta)), p.stat().st_size) for p in carpeta.rglob("*") if p.is_file())


# =====================================================================================================================
# 9.1 Histórico: disco y lector
# =====================================================================================================================

T_FEB = 1_706_745_600   # 2024-02-01 00:00 UTC


def velas_minuto(t_ini, n, precio=100.0, saltar=()):
    out = []
    for k in range(n):
        t = t_ini + k * 60
        if t in saltar:
            continue
        p = precio + k * 0.01
        out.append([t, round(p, 4), round(p + 0.5, 4), round(p - 0.5, 4), round(p + 0.1, 4), 1.0])
    return out


def sin_marca(res):
    """resumen() without the fields a rebuild cannot know exactly (timestamp and source list)."""
    return {par: {k: {c: v for c, v in d.items() if c not in ("actualizado", "fuentes")} for k, d in inter.items()} for par, inter in res.items()}


def prueba_historico_disco():
    ok(historico.mes_de(T_FEB - 1) == "2024-01" and historico.mes_de(T_FEB) == "2024-02", "mes_de en la frontera UTC de mes")

    # overlapping batches around the month border
    lote_a = velas_minuto(T_FEB - 120, 5)                    # 23:58 .. 00:02
    lote_b = [[T_FEB + 120, 50.0, 51.0, 49.0, 50.5, 2.0], [T_FEB + 180, 50.5, 51.5, 49.5, 50.0, 2.0]]   # 00:02 changed, 00:03 new
    historico.guardar("XBTEUR", 1, lote_a)
    historico.guardar("XBTEUR", 1, lote_b)
    ene = historico.leer_mes("XBTEUR", 1, "2024-01")
    feb = historico.leer_mes("XBTEUR", 1, "2024-02")
    ok([v[0] for v in ene] == [T_FEB - 120, T_FEB - 60], "guardar: las velas de enero caen en 2024-01.json")
    ok([v[0] for v in feb] == [T_FEB, T_FEB + 60, T_FEB + 120, T_FEB + 180], "guardar: las de febrero en 2024-02.json, ordenadas y sin duplicados")
    ok(feb[2][4] == 50.5, "guardar: en la vela repetida gana el lote nuevo")
    ok(all(historico.mes_de(v[0]) == "2024-01" for v in ene) and all(historico.mes_de(v[0]) == "2024-02" for v in feb), "cada fichero solo tiene velas de su mes")
    ok(historico.ruta_mes("XBTEUR", 1, "2024-02") == nucleo.DATOS_DIR / "historico" / "XBTEUR" / "1m" / "2024-02.json", "ruta_mes")

    ok(historico.validar_vela([T0, 100, 101, 99, 100, 1], 1), "validar_vela acepta una vela correcta")
    ok(not historico.validar_vela([T0 + 30, 100, 101, 99, 100, 1], 1), "validar_vela rechaza t no alineado")
    ok(not historico.validar_vela([T0, 100, 99, 101, 100, 1], 1), "validar_vela rechaza low > high")
    ok(not historico.validar_vela([T0, 100, 101, 99, 100, -1], 1), "validar_vela rechaza volumen negativo")
    ok(not historico.validar_vela([T0, float("nan"), 101, 99, 100, 1], 1), "validar_vela rechaza nan")
    ok(not historico.validar_vela([T0, 100, 101, 99, 100], 1), "validar_vela rechaza una lista de 5")

    diez = [[T_TR + i * 60, 100 + i, 102 + i, 99 + i, 101 + i, 1] for i in range(10)]
    r = historico.remuestrear(diez, 1, 5)
    ok(r == [[T_TR, 100, 106, 99, 105, 5], [T_TR + 300, 105, 111, 104, 110, 5]], "remuestrear 10 velas de 1 min -> 2 de 5 min")
    ok(len(historico.remuestrear(diez[:9], 1, 5)) == 1, "remuestrear: el cubo incompleto del final no se emite")
    r9 = historico.remuestrear(diez[:9], 1, 5, hasta_t=T_TR + 600)
    ok(len(r9) == 2 and r9[1][5] == 4, "remuestrear con hasta_t cierra el último cubo (vol 4)")
    sesenta = [[T_TR + i * 60, 100 + (i % 7), 103 + (i % 5), 98 - (i % 3), 101 + (i % 4), 1 + i % 2] for i in range(60)]
    ok(historico.remuestrear(historico.remuestrear(sesenta, 1, 5), 5, 15) == historico.remuestrear(sesenta, 1, 15), "remuestrear 1->5->15 == 1->15")
    try:
        historico.remuestrear(sesenta, 5, 7)
        ok(False, "remuestrear(5, 7) -> ValueError")
    except ValueError:
        ok(True, "remuestrear(5, 7) -> ValueError")

    ti = T_TR
    con_salto = [[ti - 120, 1, 1, 1, 1, 1], [ti - 60, 1, 1, 1, 1, 1], [ti, 1, 1, 1, 1, 1], [ti + 7260, 1, 1, 1, 1, 1], [ti + 7320, 1, 1, 1, 1, 1]]
    ok(historico.huecos(con_salto, 1) == [{"desde": ti + 60, "hasta": ti + 7260, "minutos": 120}], "huecos: salto de 2 h")
    cinco = [[ti, 1, 1, 1, 1, 1], [ti + 300, 1, 1, 1, 1, 1]]
    ok(historico.huecos(cinco, 1, minimo_s=3600) == [] and len(historico.huecos(cinco, 1, minimo_s=60)) == 1, "huecos: saltos de 5 min solo con minimo_s=60")

    # a full day of 1-min candles with a 2-hour hole inside February: 2024-01-31 20:00 -> 2024-02-01 20:00
    t_ini = T_FEB - 4 * 3600
    hueco = set(range(T_FEB + 3600, T_FEB + 3 * 3600, 60))
    dia_1m = velas_minuto(t_ini, 1440, saltar=hueco)
    limpiar_historico()
    sembrar("XBTEUR", 1, dia_1m)
    desde, hasta = T_FEB - 1800, T_FEB + 1800
    c = historico.cargar("XBTEUR", desde, hasta, 1)
    ok([v[0] for v in c] == list(range(desde, hasta, 60)), "cargar 1 min sobre dos meses: concatenado, ordenado, [desde, hasta)")
    ok(all(v[0] + 60 <= hasta for v in c) and c[-1][0] == hasta - 60, "cargar excluye la vela que cierra después de hasta")
    ref5 = [v for v in historico.remuestrear(dia_1m, 1, 5, hasta_t=hasta) if desde <= v[0] and v[0] + 300 <= hasta]
    ok(historico.cargar("XBTEUR", desde, hasta, 5) == ref5, "cargar a 5 min sin carpeta 5m == remuestrear de 1 min")
    sembrar("XBTEUR", 60, [[T_FEB + 10 * 3600, 1, 2, 0.5, 1, 1], [T_FEB + 11 * 3600, 1, 2, 0.5, 1, 1]])
    fin_1m = t_ini + 1440 * 60
    c60 = historico.cargar("XBTEUR", t_ini, fin_1m, 60)
    ref60 = historico.remuestrear(dia_1m, 1, 60, hasta_t=fin_1m)
    ok(len(c60) == 22 and c60 == ref60, "cargar a 60 min: gana la cobertura del 1 min (22 horas, 2 de hueco)")
    ok(historico.rango_disponible("XBTEUR", 60) == (t_ini, fin_1m), "rango_disponible(60) coincide con el del 1 min")

    r = historico.resumen()["XBTEUR"]["1"]
    ok(r["velas"] == 1320 and r["desde"] == "2024-01-31" and r["hasta"] == "2024-02-01", "resumen: velas, desde y hasta")
    ok(r["desde_t"] == t_ini and r["hasta_t"] == fin_1m, "resumen: desde_t y hasta_t")
    ok(r["huecos_largos"] == 1, "resumen: un hueco largo")
    completo = sin_marca(historico.resumen())
    (nucleo.DATOS_DIR / "historico" / "estado.json").unlink()
    ok(sin_marca(historico.resumen()) == completo, "resumen() reconstruye estado.json si falta")
    ok(sin_marca(historico.reindexar(avisar=lambda m: None)) == completo, "reindexar() idéntico")


# =====================================================================================================================
# 9.2 Histórico: fuentes
# =====================================================================================================================

def prueba_historico_fuentes():
    ok(len(exchange_historico("XBTEUR", 1, desde=AHORA - 10 ** 7)) == 720, "exchange falso: tope de 720 velas")
    log = []
    res = historico.actualizar("XBTEUR", intervalos=[1, 60], dias=0, avisar=log.append, ahora=AHORA)
    ok(isinstance(res, dict) and "XBTEUR" in res and not res["XBTEUR"].get("error") and not res["XBTEUR"].get("ocupado"), "actualizar devuelve el resultado por par sin error")
    cola = historico.cargar("XBTEUR", AHORA - 720 * 60, AHORA + 60, 1)
    ok(640 <= len(cola) <= 720 and not any(v[5] == 0 for v in cola), f"cola OHLC de 1 min sin velas de volumen 0 ({len(cola)} velas)")
    cola60 = historico.cargar("XBTEUR", AHORA - 720 * 3600, AHORA + 3600, 60)
    ok(719 <= len(cola60) <= 720, f"cola OHLC de 60 min completa ({len(cola60)} velas)")
    r = historico.resumen()["XBTEUR"]
    ok("ohlc" in r["1"]["fuentes"] and "ohlc" in r["60"]["fuentes"], "resumen ve la fuente ohlc")
    ok(log, "actualizar escribe en el registro")

    filas = [["100", "1.0", T_TR + 1.0, "b", "m", "", 1], ["101", "0.5", T_TR + 20.0, "b", "m", "", 2],
             ["99.5", "2.0", T_TR + 45.0, "s", "m", "", 3], ["100.5", "1.0", T_TR + 59.0, "b", "l", "", 4],
             ["102", "0.25", T_TR + 120.0, "b", "m", "", 5], ["101", "0.25", T_TR + 130.0, "s", "m", "", 6],
             ["103", "0.5", T_TR + 170.0, "b", "m", "", 7]]
    ok(historico.agregar_trades(filas) == [[T_TR, 100, 101, 99.5, 100.5, 4.5], [T_TR + 120, 102, 103, 101, 103, 1.0]], "agregar_trades: dos minutos, sin vela en el minuto vacío")

    # Trades backfill: three stretches of 9000 s of the fake tape
    cfg1 = cfg_hist(volcar_cada=1)
    log = []
    r = historico.rellenar_con_trades("XBTEUR", T_TR, T_TR + 9000, avisar=log.append, cfg=cfg1)
    velas = historico.cargar("XBTEUR", T_TR, T_TR + 9000, 1)
    ok(len(velas) == 150 and r["terminado"] is True, f"rellenar_con_trades: 150 velas de 1 min, terminado ({r})")
    ok(cerca(sum(v[5] for v in velas), 300.0, 1e-6), "rellenar_con_trades: volumen = 0,1 x 3000 operaciones (sin doble cuenta del cursor)")
    ok(r["llamadas"] in (3, 4), f"rellenar_con_trades: 3 llamadas (+1 que corta) ({r['llamadas']})")

    llamadas = {"n": 0}

    def con_limite(par, desde_ns):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("Kraken: EAPI:Rate limit exceeded")
        return trades_falsos(par, desde_ns)

    historico.descargar_trades_fn = con_limite
    try:
        r = historico.rellenar_con_trades("XBTEUR", T_TR + 9000, T_TR + 18000, avisar=log.append, cfg=cfg1)
    finally:
        historico.descargar_trades_fn = trades_falsos
    velas = historico.cargar("XBTEUR", T_TR + 9000, T_TR + 18000, 1)
    ok(len(velas) == 150 and cerca(sum(v[5] for v in velas), 300.0, 1e-6) and r["terminado"], "rate limit en la 2.ª llamada: reintenta y el total sale igual")

    estado = {"n": 0}

    def cae_siempre(par, desde_ns):
        estado["n"] += 1
        if estado["n"] == 1:
            return trades_falsos(par, desde_ns)
        raise requests.ConnectionError("sin red")

    historico.descargar_trades_fn = cae_siempre
    try:
        r = historico.rellenar_con_trades("XBTEUR", T_TR + 18000, T_TR + 27000, avisar=log.append, cfg=cfg1)
    finally:
        historico.descargar_trades_fn = trades_falsos
    cursor = mercado._leer_json(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "cursor_trades.json", None)
    last_1 = str((T_TR + 18000 + 2997) * 10 ** 9)
    ok(r["terminado"] is False and estado["n"] in (8, 9), f"sin red: tras 7 intentos seguidos termina con terminado False ({estado['n']} llamadas)")
    ok(isinstance(cursor, dict) and str(cursor.get("last", "")).isdigit() and (T_TR + 18000) * 10 ** 9 - 1 <= int(cursor["last"]) <= int(last_1),
       f"cursor_trades.json con el last de la 1.ª llamada ({cursor})")
    r = historico.rellenar_con_trades("XBTEUR", T_TR + 18000, T_TR + 27000, avisar=log.append, cfg=cfg1)
    velas = historico.cargar("XBTEUR", T_TR + 18000, T_TR + 27000, 1)
    vol = sum(v[5] for v in velas)
    ok(r["terminado"] and len(velas) == 150 and 298.0 <= vol <= 300.0 + 1e-6, f"reanuda desde el cursor sin repetir velas (150 velas, volumen {vol:.1f})")

    # CSV import
    t_csv = 1_583_020_800   # 2020-03-01 00:00 UTC, multiple of 300
    orden = [3, 0, 5, 1, 7, 2, 6, 4]
    filas = [[t_csv + 300 * k, 100 + k, 101 + k, 99 + k, 100.5 + k, 1 + k, 10] for k in orden]
    filas.append([t_csv + 300 * 5, 200, 201, 199, 200.5, 9, 10])      # duplicate t (the last one wins)
    filas.append([t_csv + 300 * 8, 100, 99, 101, 100, 1, 10])          # low > high

    def escribir_csv(ruta, filas, factor=1):
        with open(ruta, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume", "trades"])
            for f in filas:
                w.writerow([f[0] * factor] + f[1:])

    ruta_csv = TMP / "XBTEUR_5.csv"
    escribir_csv(ruta_csv, filas)
    r = historico.importar_csv(ruta_csv, avisar=lambda m: None)
    esperado = {"par": "XBTEUR", "intervalo": 5, "filas": 10, "guardadas": 8, "descartadas": 1, "duplicadas": 1}
    ok({k: r.get(k) for k in esperado} == esperado, f"importar_csv: {r}")
    mes = historico.leer_mes("XBTEUR", 5, "2020-03")
    ok([v[0] for v in mes] == [t_csv + 300 * k for k in range(8)] and mes[5][1] == 200, "importar_csv: fichero mensual ordenado, gana la última fila repetida")
    shutil.rmtree(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "5m")
    r = historico.importar_csv(ruta_csv, desde="2020-03-01", avisar=lambda m: None)
    ok(r["guardadas"] == 8, "importar_csv con desde el mismo día: 8")
    shutil.rmtree(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "5m")
    # `desde` is a UTC date: use a file in two days so that a date cut leaves out 3 rows
    filas2 = [[t_csv - 86400 + 300 * k, 100, 101, 99, 100, 1, 1] for k in range(3)] + [[t_csv + 300 * k, 100, 101, 99, 100, 1, 1] for k in range(5)]
    escribir_csv(ruta_csv, filas2)
    r = historico.importar_csv(ruta_csv, desde="2020-03-01", avisar=lambda m: None)
    ok(r["guardadas"] == 5 and r["filas"] == 8, "importar_csv: desde deja fuera 3 filas -> guardadas 5")
    shutil.rmtree(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "5m")
    escribir_csv(ruta_csv, filas, factor=1000)
    r = historico.importar_csv(ruta_csv, avisar=lambda m: None)
    ok({k: r.get(k) for k in esperado} == esperado and historico.leer_mes("XBTEUR", 5, "2020-03")[0][0] == t_csv, "importar_csv con timestamps en milisegundos: mismo resultado")
    r = historico.importar_carpeta(TMP, ["XBTEUR"], avisar=lambda m: None)
    ok(len(r) == 1 and r[0]["par"] == "XBTEUR" and r[0]["intervalo"] == 5, "importar_carpeta encuentra XBTEUR_5.csv")
    shutil.rmtree(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "5m")

    # consolidar_vigia: a closed local day (two days ago, outside the OHLC tail) and today
    inicio_hoy = int(mercado._inicio_dia(AHORA))
    inicio_cerrado = inicio_hoy - 2 * 86400
    cerrado = [[inicio_cerrado + k * 60, 12345.0, 12346.0, 12344.0, 12345.0, 0.0 if k == 7 else 1.0] for k in range(60)]
    hoy = [[inicio_hoy + k * 60, 12345.0, 12346.0, 12344.0, 12345.0, 1.0] for k in range(30)]
    mercado.guardar_velas("XBTEUR", cerrado + hoy)
    antes = listado(nucleo.DATOS_DIR / "velas")
    n = historico.consolidar_vigia("XBTEUR", avisar=lambda m: None)
    ok(n == 59, f"consolidar_vigia: 59 velas del día cerrado (sin la de volumen 0) ({n})")
    guardadas = {v[0]: v for v in historico.cargar("XBTEUR", inicio_cerrado, inicio_cerrado + 3600, 1)}
    ok(len(guardadas) == 59 and inicio_cerrado + 7 * 60 not in guardadas and all(v[4] == 12345.0 for v in guardadas.values()), "consolidar_vigia: las 59 velas están en el histórico")
    de_hoy = historico.cargar("XBTEUR", inicio_hoy, inicio_hoy + 1800, 1)
    ok(not any(v[4] == 12345.0 for v in de_hoy), "consolidar_vigia: las de hoy no se consolidan")
    ok(historico.consolidar_vigia("XBTEUR", avisar=lambda m: None) == 0, "consolidar_vigia: segunda llamada no repite")
    ok(listado(nucleo.DATOS_DIR / "velas") == antes, "consolidar_vigia no toca datos/velas")

    # podar_velas with 0 = never
    dia3 = mercado.dia_local(AHORA - 3 * 86400)
    mercado._escribir_json(nucleo.DATOS_DIR / "velas" / "XBTEUR" / f"{dia3}.json", [[AHORA - 3 * 86400, 1, 1, 1, 1, 1]])
    hist_antes = listado(nucleo.DATOS_DIR / "historico")
    borrados = mercado.podar_velas("XBTEUR", 1)
    ok(borrados >= 1 and not (nucleo.DATOS_DIR / "velas" / "XBTEUR" / f"{dia3}.json").exists(), "podar_velas(1) borra el fichero de hace 3 días")
    ok(listado(nucleo.DATOS_DIR / "historico") == hist_antes, "podar_velas no toca datos/historico")
    hoy_json = nucleo.DATOS_DIR / "velas" / "XBTEUR" / f"{mercado.dia_local(AHORA)}.json"
    ok(mercado.podar_velas("XBTEUR", 0) == 0 and hoy_json.exists(), "podar_velas(0) no borra nada")
    ok(mercado.configuracion({"conservar_dias": 0})["conservar_dias"] == 0, "configuracion: conservar_dias 0 se respeta")
    ok(mercado.configuracion({})["conservar_dias"] == 400, "configuracion: conservar_dias ausente -> 400")

    # lock between processes
    ocupado = nucleo.DATOS_DIR / "historico" / "XBTEUR" / "ocupado.json"
    mercado._escribir_json(ocupado, {"pid": 99999, "inicio": time.time(), "latido": time.time()})
    hist_antes = listado(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "1m")
    log = []
    r = historico.actualizar("XBTEUR", intervalos=[1], dias=0, avisar=log.append, ahora=AHORA)
    ok(r.get("XBTEUR", {}).get("ocupado") is True, "bloqueo: con ocupado.json reciente actualizar dice ocupado")
    ok(listado(nucleo.DATOS_DIR / "historico" / "XBTEUR" / "1m") == hist_antes and ocupado.exists(), "bloqueo: no toca nada ni borra el bloqueo ajeno")
    mercado._escribir_json(ocupado, {"pid": 99999, "inicio": time.time() - 1200, "latido": time.time() - 600})
    r = historico.actualizar("XBTEUR", intervalos=[1], dias=0, avisar=log.append, ahora=AHORA)
    ok(not r["XBTEUR"].get("ocupado") and not r["XBTEUR"].get("error"), "bloqueo: con latido de hace 10 min procede")
    ok(not ocupado.exists(), "bloqueo: al terminar no queda ocupado.json")
    # one writer per pair ACROSS processes (§4.7); inside the process the lock is reentrant, because actualizar()
    # holds it while it calls rellenar_con_trades(), which takes it as well
    with historico.ocupar("XBTEUR"):
        ok(ocupado.exists(), "ocupar escribe ocupado.json")
        try:
            with historico.ocupar("XBTEUR"):
                ok(ocupado.exists(), "ocupar anidado en el mismo proceso: permitido (actualizar envuelve a rellenar_con_trades)")
            ok(ocupado.exists(), "ocupar anidado: al salir el interior el bloqueo sigue vivo")
        except historico.Ocupado:
            ok(False, "ocupar anidado en el mismo proceso: permitido (actualizar envuelve a rellenar_con_trades)")
    ok(not ocupado.exists(), "ocupar borra el bloqueo al salir")
    mercado._escribir_json(ocupado, {"pid": 99999, "inicio": time.time(), "latido": time.time()})
    try:
        with historico.ocupar("XBTEUR"):
            ok(False, "ocupar con bloqueo ajeno vivo -> Ocupado")
    except historico.Ocupado as err:
        ok("99999" in str(err) and str(ocupado) in str(err), "ocupar con bloqueo ajeno vivo -> Ocupado (con pid y ruta)")
    ok(ocupado.exists(), "ocupar no borra el bloqueo ajeno")
    ocupado.unlink()

    # validar on a clean pair: 3 h of 1 min + native 60m equal to its resampling, plus the watcher's candles
    t_v = (AHORA - 3 * 86400) // 3600 * 3600
    tres_h = velas_minuto(t_v, 180, precio=3000.0)
    sembrar("ETHEUR", 1, tres_h)
    sesenta = historico.remuestrear(tres_h, 1, 60)
    sembrar("ETHEUR", 60, sesenta)
    mercado.guardar_velas("ETHEUR", tres_h[:90] + [[t_v + 90 * 60, 1.0, 1.0, 1.0, 1.0, 0.0]])
    v = historico.validar("ETHEUR", avisar=lambda m: None, ahora=AHORA)
    ok(v["concordancia_1m_60m_pct"] == 100.0 and v["horas_discordantes"] == [], f"validar: 1 min y 60 min concuerdan al 100 % ({v.get('concordancia_1m_60m_pct')})")
    ok(v["discrepancias_vigia"] == 0, "validar: vigía y histórico coinciden (ignorando volumen 0)")
    ok(v["intervalos"]["1"]["velas"] == 180 and v["intervalos"]["1"]["ficheros_malos"] == [] and v["intervalos"]["1"]["desordenadas"] == 0, "validar: ficheros sanos")
    ok(isinstance(v.get("frescura_min"), int) and v["frescura_min"] >= 4000, "validar: frescura en minutos")
    malo = [list(c) for c in sesenta]
    malo[1][4] = malo[1][4] + 10
    mercado._escribir_json(historico.ruta_mes("ETHEUR", 60, historico.mes_de(t_v)), malo)
    v = historico.validar("ETHEUR", avisar=lambda m: None, ahora=AHORA)
    ok(cerca(v["concordancia_1m_60m_pct"], 66.67, 0.01) and v["horas_discordantes"] == [sesenta[1][0]], f"validar: una hora corrompida -> 66,67 % y la hora listada ({v.get('concordancia_1m_60m_pct')})")

    inf = nucleo.Informe()
    historico.comprobar(inf)
    sec = next((s for s in inf.secciones if s["titulo"] == "Histórico"), None)
    ok(sec is not None and len(sec["items"]) >= 2, "comprobar(inf) añade la sección Histórico con un ítem por par")


# =====================================================================================================================
# 9.3 Backtest: motor
# =====================================================================================================================

def fija(ordenes, **kw):
    return estrategias.EstrategiaFija(ordenes, **{"marco": 60, "calentamiento": 0, **kw})


def barra(k, o, h, l, c, t0=T0, marco=60):
    return [t0 + k * marco * 60, o, h, l, c, 1]


def prueba_motor():
    b0 = barra(0, 100, 101, 99, 100)
    b1 = barra(1, 101, 102, 100, 101)
    b2 = barra(2, 100, 100.5, 94, 95)
    compra = {0: {"accion": "comprar", "stop": 95}}
    r = backtest.simular([b0, b1, b2], fija(compra), {}, CFG)
    op = r["operaciones"][0]
    ok(len(r["operaciones"]) == 1 and cerca(op["entrada"], 101.0505, 1e-9), "llenado: entrada 101,0505 (open + deslizamiento)")
    ok(cerca(op["cantidad"], 14.431426, 1e-5), "llenado: cantidad 14,431426 por la regla del 1 %")
    ok(op["entrada_t"] == T0 + 3600 and op["entrada_i"] == 1, "llenado: señal al cierre, ejecución al open siguiente")
    ok(op["motivo"] == "stop" and cerca(op["salida"], 94.905, 1e-9), "stop: salida 94,905")
    ok(cerca(op["pnl"], -100.0, 1e-6) and cerca(op["R"], -1.0, 1e-9), "stop: pnl -100 € y R -1,000 exactos (comisiones incluidas)")
    ok(cerca(op["comisiones"], 11.3117, 0.01) and cerca(op["deslizamiento"], 2.0998, 0.01), "stop: comisiones 11,31 y deslizamiento 2,10")
    ok(cerca(r["capital_final"], 9900.0, 1e-6) and op["barras"] == 1 and op["ventana"] == 0, "capital final 9.900 y 1 barra")
    ok(op["riesgo"] == 100.0 and op["capital_antes"] == 10000.0 and op["stop"] == 95 and op["salida_t"] == T0 + 7200, "registro de la operación")

    r = backtest.simular([b0, b1, barra(2, 90, 91, 88, 89)], fija(compra), {}, CFG)
    op = r["operaciones"][0]
    ok(cerca(op["salida"], 89.91, 1e-9) and cerca(op["pnl"], -171.80, 0.01) and cerca(op["R"], -1.718, 0.001), "hueco bajo el stop: salida al open con deslizamiento de stop")

    con_obj = {0: {"accion": "comprar", "stop": 95, "objetivo": 108}}
    r = backtest.simular([b0, b1, barra(2, 100, 110, 94, 105)], fija(con_obj), {}, CFG)
    ok(r["operaciones"][0]["motivo"] == "stop", "stop y objetivo en la misma barra: gana el stop")
    r = backtest.simular([b0, b1, barra(2, 100, 110, 99, 105)], fija(con_obj), {}, CFG)
    op = r["operaciones"][0]
    ok(op["motivo"] == "objetivo" and op["salida"] == 108.0 and cerca(op["pnl"], 88.22, 0.02) and cerca(op["R"], 0.882, 0.001), "objetivo: orden limitada sin deslizamiento")
    r = backtest.simular([b0, b1, barra(2, 109, 110, 99, 105)], fija(con_obj), {}, CFG)
    op = r["operaciones"][0]
    ok(op["salida"] == 109.0 and cerca(op["R"], 1.026, 0.001), "objetivo: abre por encima -> se llena al open")

    trailing = {0: {"accion": "comprar", "stop": 95}, 1: {"stop": 98}, 2: {"stop": 97}}
    r = backtest.simular([b0, b1, barra(2, 101, 102, 100, 101), barra(3, 99, 99.5, 97.5, 98)], fija(trailing), {}, CFG)
    op = r["operaciones"][0]
    ok(op["stop_final"] == 98 and cerca(op["salida"], 97.902, 1e-9) and cerca(op["R"], -0.569, 0.001), "trailing: el stop solo sube (98), salida 97,902")

    planas = [barra(k, 100, 101, 99, 100) for k in range(5)]
    malas = {0: {"accion": "comprar", "stop": None}, 1: {"accion": "comprar", "stop": 102}, 2: {"accion": "comprar", "stop": 99.9}}
    r = backtest.simular(planas, fija(malas), {}, CFG)
    ok(r["rechazadas"] == 3 and r["rechazos"] == {"sin_stop": 1, "stop_alto": 2} and r["operaciones"] == [], "regla 2: sin stop o stop demasiado cerca -> rechazadas")

    r = backtest.simular([barra(0, 100, 101, 99, 100), barra(1, 100, 101, 99.8, 100), barra(2, 100, 101, 99.8, 100)],
                         fija({0: {"accion": "comprar", "stop": 99.5}}), {}, CFG)
    op = r["operaciones"][0]
    ok(op["cantidad"] * op["entrada"] <= 3000 + 1e-6 and cerca(op["cantidad"], 29.985, 0.001), "regla 5: como mucho el 30 % del capital en el activo")
    t = backtest.tamano(10000, 2000, 100, 99.5, CFG)
    ok(cerca(t[0], 2000 / (100.05 * 1.004), 1e-6) and t[3] is None, "tamano: sin apalancamiento (limita el efectivo)")
    ok(backtest.tamano(10000, 10000, 100, 99.5, cfg_bt(minimo_orden_eur=5000))[3] == "minimo", "tamano: bajo el mínimo de orden -> 'minimo'")
    ok(backtest.tamano(10000, 10000, 100, None, CFG)[3] == "sin_stop" and backtest.tamano(10000, 10000, 100, 100.2, CFG)[3] == "stop_alto", "tamano: motivos sin_stop y stop_alto")

    # rule 3: five -1R stops in one UTC day: the fifth does not run, the next day does. The stop sits 5 % below the
    # open (like the §6.5 worked example) so the 30 % cap of rule 5 does not bind and -1R is exactly -1 % of capital
    dia = [barra(k, 100, 101, 94, 100) for k in range(30)]
    ordenes = {k: {"accion": "comprar", "stop": 95} for k in (0, 1, 2, 3, 4, 24)}
    r = backtest.simular(dia, fija(ordenes), {}, CFG)
    ops = r["operaciones"]
    ok(len(ops) == 5 and [o["entrada_i"] for o in ops] == [1, 2, 3, 4, 25], f"regla 3: 4 operaciones el primer día, la 5.ª no, la del día siguiente sí ({[o['entrada_i'] for o in ops]})")
    ok(r["paradas_dia"] == 1 and cerca(ops[3]["capital_antes"] * 0.99, 10000 * 0.99 ** 4, 0.01), "regla 3: una parada del día tras -3,94 %")
    ordenes = {0: {"accion": "comprar", "stop": 95}, 1: {"accion": "comprar", "stop": 95}, 2: {"accion": "comprar", "stop": 95}, 3: {"accion": "comprar", "stop": 96}}
    velas = [barra(k, 100, 101, 94, 100) for k in range(4)] + [barra(4, 100, 100.5, 97.5, 98), barra(5, 98, 99, 97, 98), barra(6, 98, 99, 97, 98)]
    r = backtest.simular(velas, fija(ordenes), {}, CFG)
    ops = r["operaciones"]
    ok(len(ops) == 4 and ops[3]["motivo"] == "parada_dia" and ops[3]["salida_i"] == 5 and cerca(ops[3]["salida"], 98 * 0.9995, 1e-9), "regla 3: con posición abierta se cierra al open siguiente con motivo parada_dia")

    # rule 4: -1R a day for 14 days with the daily stop disabled: the 13th trips the -12 % switch
    cfg4 = cfg_bt(parada_dia_pct=100)
    velas = [barra(k, 100, 101, 94, 100) for k in range(24 * 14 + 3)]
    ordenes = {24 * d: {"accion": "comprar", "stop": 95} for d in range(14)}
    r = backtest.simular(velas, fija(ordenes), {}, cfg4)
    ok(len(r["operaciones"]) == 13 and r["apagones"] == 1, f"regla 4: 13 operaciones y apagado (0,99^13 = 12,25 % de caída) ({len(r['operaciones'])})")
    ok(cerca(r["capital_final"], 10000 * 0.99 ** 13, 0.01) and r["estado"]["apagado"] is True, "regla 4: capital final 8.775 y estado apagado")
    r2 = backtest.simular(velas, fija(ordenes), {}, cfg4, estado=r["estado"])
    ok(r2["operaciones"] == [] and r2["estado"]["apagado"] is True, "regla 4: con el estado apagado arrastrado no entra ninguna orden")
    velas = [barra(k, 100, 101, 94, 100) for k in range(24 * 12 + 1)] + [barra(24 * 12 + 1, 100, 100.5, 90.2, 90.5), barra(24 * 12 + 2, 90.5, 91, 90, 90.5), barra(24 * 12 + 3, 90.5, 91, 90, 90.5)]
    ordenes = {24 * d: {"accion": "comprar", "stop": 95} for d in range(12)}
    ordenes[24 * 12] = {"accion": "comprar", "stop": 90}
    r = backtest.simular(velas, fija(ordenes), {}, cfg4)
    ok(len(r["operaciones"]) == 13 and r["operaciones"][-1]["motivo"] == "apagado" and r["apagones"] == 1, "regla 4: con posición abierta al apagarse -> motivo apagado")

    # exits by time, by signal and at the end of the slice
    tres = [barra(0, 100, 101, 99, 100), barra(1, 100, 101, 99, 100), barra(2, 100, 101, 99, 100), barra(3, 100, 101, 99, 100)]
    r = backtest.simular(tres, fija({0: {"accion": "comprar", "stop": 90}, 2: {"accion": "vender", "motivo": "tiempo"}}), {}, CFG)
    ok(r["operaciones"][0]["motivo"] == "tiempo" and r["operaciones"][0]["salida_i"] == 3, "salida por tiempo al open siguiente")
    r = backtest.simular(tres, fija({0: {"accion": "comprar", "stop": 90}, 2: {"accion": "vender", "motivo": "senal"}}), {}, CFG)
    ok(r["operaciones"][0]["motivo"] == "senal", "salida por señal")
    r = backtest.simular(tres[:3], fija({0: {"accion": "comprar", "stop": 90}}), {}, CFG)
    op = r["operaciones"][0]
    ok(op["motivo"] == "fin" and cerca(op["salida"], 100 * 0.9995, 1e-9) and op["comisiones"] > 0, "posición abierta al final: cierre 'fin' con costes")

    # equity curve
    velas = [barra(k, 100, 101, 99, 100 + k * 0.1) for k in range(10)]
    r = backtest.simular(velas, fija({3: {"accion": "comprar", "stop": 90}}, calentamiento=2), {}, CFG, desde_t=T0 + 4 * 3600)
    ok([p[0] for p in r["curva"]] == [v[0] for v in velas[4:]] and r["barras"] == 6, "curva: una entrada por barra procesada, sin calentamiento ni barras antes de desde_t")
    r = backtest.simular(velas, fija({3: {"accion": "comprar", "stop": 90}}), {}, CFG)
    op = r["operaciones"][0]
    efectivo = 10000 - op["cantidad"] * op["entrada"] * 1.004
    punto = next(p for p in r["curva"] if p[0] == velas[5][0])
    ok(cerca(punto[1], efectivo + op["cantidad"] * velas[5][4], 1e-6), "curva: con posición equity = efectivo + cantidad x cierre")
    ok(r["barras_en_posicion"] >= 5 and r["barras"] == 10, "barras y barras en posición")


# =====================================================================================================================
# 9.4 Backtest: métricas y walk-forward
# =====================================================================================================================

def op_falsa(pnl, R, capital=10000.0, k=0):
    return {"ventana": 1, "entrada_t": T0 + k * 7200, "entrada_i": k * 2, "salida_t": T0 + k * 7200 + 3600, "salida_i": k * 2 + 1,
            "entrada": 100.0, "salida": 100.0 + pnl / 10, "stop": 99.0, "stop_final": 99.0, "objetivo": None, "cantidad": 10.0,
            "capital_antes": capital, "riesgo": 100.0, "pnl": pnl, "R": R, "comisiones": 0.8, "deslizamiento": 0.1,
            "barras": 1, "motivo": "senal", "fecha_entrada": "", "fecha_salida": ""}


def prueba_metricas_wf():
    ok(backtest.max_drawdown([[0, 100], [1, 120], [2, 90], [3, 110]]) == 25.0, "max_drawdown 25 %")
    curva = [[d * 86400, e] for d, e in enumerate([100, 120, 90, 110, 125])]
    ok(cerca(backtest.duracion_drawdown(curva), 3.0, 1e-9), "duracion_drawdown 3 días")

    rend = [0.01, -0.005, 0.02, -0.01, 0.005]
    eq = [100.0]
    for x in rend:
        eq.append(eq[-1] * (1 + x))
    curva = []
    for d, e in enumerate(eq):   # two points a day: the midday one must be ignored (last close of the day counts)
        curva.append([T0 + d * 86400 + 3600, e * 1.5])
        curva.append([T0 + d * 86400 + 7200, e])
    esperado = statistics.mean(rend) / statistics.stdev(rend) * math.sqrt(365)
    ok(cerca(backtest.sharpe_diario(curva, "utc", min_dias=1), esperado, 1e-3) and cerca(esperado, 6.4018, 1e-3), "sharpe_diario 6,40 sobre los cierres diarios")
    ok(backtest.sharpe_diario(curva, "utc", min_dias=30) is None, "sharpe_diario: menos de 30 días -> None")
    ok(backtest.sharpe_diario([[T0 + d * 86400, 100.0] for d in range(40)], "utc", min_dias=1) is None, "sharpe_diario: curva plana -> None")
    ok(backtest.rendimientos_diarios(curva, "utc") and all(cerca(a, b, 1e-9) for a, b in zip(backtest.rendimientos_diarios(curva, "utc"), rend)), "rendimientos_diarios")

    ops = [op_falsa(p, r, k=k) for k, (p, r) in enumerate([(200, 2), (-100, -1), (100, 1), (-100, -1), (-100, -1)])]
    curva = [[T0, 10000.0], [T0 + 86400, 10000.0]]
    m = backtest.metricas(ops, curva, 10000, CFG)
    ok(m["n"] == 5 and m["profit_factor"] == 1.0 and cerca(m["win_rate"], 0.4, 1e-9), "metricas: n, profit factor 1,0, win rate 0,4")
    ok(cerca(m["expectativa_R"], 0.0, 1e-9) and cerca(m["expectativa_pct"], 0.0, 1e-9), "metricas: expectativa 0 R y 0 %")
    ok(m["racha_perdedora_max"] == 2 and m["mejor_R"] == 2 and m["peor_R"] == -1, "metricas: racha perdedora 2, mejor 2, peor -1")
    ok(m["ganadoras"] == 2 and m["perdedoras"] == 3 and cerca(m["media_R_ganadoras"], 1.5, 1e-9) and cerca(m["media_R_perdedoras"], -1.0, 1e-9), "metricas: ganadoras y perdedoras")
    ok(backtest.metricas([op_falsa(100, 1), op_falsa(50, 0.5, k=1)], curva, 10000, CFG)["profit_factor"] is None, "metricas: sin perdedoras -> profit_factor None")
    m0 = backtest.metricas([], curva, 10000, CFG)
    ok(m0["n"] == 0 and m0["expectativa_R"] == 0 and m0["profit_factor"] is None and m0["sharpe_diario"] is None, "metricas con n = 0 no explota")
    b1 = backtest.bootstrap_expectativa([2, -1, 1, -1, -1, 0.5, 1.5], semilla=0)
    b2 = backtest.bootstrap_expectativa([2, -1, 1, -1, -1, 0.5, 1.5], semilla=0)
    ok(b1 == b2 and b1[0] <= b1[1] <= b1[2], "bootstrap_expectativa determinista y ordenado")

    v = backtest.ventanas(T0, T0 + 730 * 86400, 180, 60)
    ok(len(v) == 9 and all(v[k + 1]["oos"][0] == v[k]["oos"][1] for k in range(8)) and v[1]["is"][1] == v[0]["oos"][1], "ventanas: 9 en dos años, OOS encadenadas")
    ok(v[0]["is"] == [T0, T0 + 180 * 86400] and v[0]["oos"] == [T0 + 180 * 86400, T0 + 240 * 86400] and v[0]["k"] == 1, "ventanas: la primera IS/OOS")
    ok(len(backtest.ventanas(T0, T0 + 730 * 86400, 180, 60, oos_min_dias=5)) == 10, "ventanas: con oos_min_dias 5 entra la parcial")
    ok(len(backtest.ventanas(T0, T0 + 420 * 86400, 180, 60)) == 4, "ventanas: 420 días -> exactamente 4")
    velas = [barra(k, 100, 101, 99, 100) for k in range(100)]
    rec = backtest.recorte(velas, T0 + 20 * 3600, T0 + 30 * 3600, 5)
    ok(rec[0][0] == T0 + 15 * 3600 and rec[-1][0] == T0 + 29 * 3600 and len(rec) == 15, "recorte con calentamiento")

    # seleccionar on the breakout series: buy at 12:30 UTC, stop at stop_pct, out after 38 bars
    rot = serie_sintetica(240, 15, 5, "rotura")

    def guion(i, velas, ctx, pos, params, memoria):
        t, c = velas[i][0], velas[i][4]
        if pos is None and (t % 86400) == 50 * 900:
            return {"accion": "comprar", "stop": c * (1 - params["stop_pct"])}
        if pos is not None and i - pos["entrada_i"] >= 38:
            return {"accion": "vender", "motivo": "tiempo"}
        return None

    e = estrategias.EstrategiaFija(guion, marco=15, calentamiento=0)
    e.rejilla = {"stop_pct": [0.01, 0.05]}
    e.defecto = {"stop_pct": 0.01}
    ventana = backtest.ventanas(T0, T0 + 240 * 86400, 180, 60)[0]
    params, tabla, por_defecto = backtest.seleccionar(rot, e, CFG, ventana, lambda m: None)
    ok(len(tabla) == 2 and all(f["n"] >= CFG["min_operaciones_is"] for f in tabla), f"seleccionar: tabla IS con las dos combinaciones ({[(f['parametros'], f['n']) for f in tabla]})")
    mejor = max(tabla, key=lambda f: f["expectativa_R"])
    ok(params == mejor["parametros"] and por_defecto is False, f"seleccionar elige la mayor expectativa_R ({params})")
    e.min_ops_is = 10 ** 6
    params, tabla, por_defecto = backtest.seleccionar(rot, e, CFG, ventana, lambda m: None)
    ok(por_defecto is True and params == e.defecto, "seleccionar sin candidatas -> por defecto")

    # walk_forward on the breakout series with rotura_dia
    rot = serie_sintetica(420, 15, 3, "rotura")
    est = estrategias.REGISTRO["rotura_dia"]
    wf = backtest.walk_forward(rot, est, CFG, T0, T0 + 420 * 86400, avisar=lambda m: None)
    vs = wf["ventanas"]
    ok(len(vs) == 4 and all("parametros" in v and "is_tabla" in v and "oos_m" in v for v in vs), "walk_forward: 4 ventanas con parámetros y tablas")
    ops = wf["operaciones"]
    ok(ops and ops[0]["entrada_t"] >= vs[0]["oos"][0] and all(o["ventana"] in (1, 2, 3, 4) for o in ops), "walk_forward: la primera operación OOS empieza en la OOS 1")
    bien = True
    for v in vs:
        de_k = [o for o in ops if o["ventana"] == v["k"]]
        if not de_k:
            continue
        bien &= all(v["oos"][0] <= o["entrada_t"] and o["salida_t"] < v["oos"][1] for o in de_k)
        bien &= cerca(v["oos_m"]["pnl"], sum(o["pnl"] for o in de_k), 0.5)
        ultimo_t = max(x["salida_t"] for x in de_k)
        bien &= all(o["salida_t"] == ultimo_t for o in de_k if o["motivo"] == "fin")
    ok(bien, "walk_forward: operaciones dentro de su OOS, pnl por ventana y cierre forzoso 'fin' al final")
    ok(cerca(wf["capital_final"], CFG["capital_inicial"] + sum(o["pnl"] for o in ops), 2.0), "walk_forward: capital cosido = capital inicial + suma de pnl")
    ok(wf["is_total"]["n"] > 0 and wf["apagones"] == 0, "walk_forward: is_total y sin apagados")
    wf2 = backtest.walk_forward(rot, est, cfg_bt(apagado_pct=0.01), T0, T0 + 420 * 86400, avisar=lambda m: None)
    ok(wf2["ventanas"][0]["oos_m"]["apagones"] == 1 and wf2["apagones"] == 1 and sum(v["oos_m"]["n"] for v in wf2["ventanas"][1:]) == 0, "walk_forward: el apagado se arrastra entre ventanas")

    # no look-ahead, every strategy of the registry
    plana = serie_sintetica(60, 15, 11, "plana")
    n = len(plana)
    for eid, est in estrategias.REGISTRO.items():
        ctx = est.preparar(plana, est.defecto, "utc")
        bien, stops_bien = True, True
        for i in (est.calentamiento + 5, n // 2, n - 2):
            corto = plana[:i + 1]
            ctx_c = est.preparar(corto, est.defecto, "utc")
            for k in ctx:
                a, b = ctx_c[k][i], ctx[k][i]
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    bien &= cerca(a, b, 1e-9)
                else:
                    bien &= a == b
            s1 = est.senal(i, corto, ctx_c, None, est.defecto, {})
            s2 = est.senal(i, plana, ctx, None, est.defecto, {})
            bien &= s1 == s2
        memoria = {}
        for i in range(est.calentamiento, n):
            s = est.senal(i, plana, ctx, None, est.defecto, memoria)
            if s and s.get("accion") == "comprar":
                stops_bien &= s["stop"] is not None and s["stop"] < plana[i][4]
        ok(bien, f"{eid}: preparar y senal no miran al futuro")
        ok(stops_bien, f"{eid}: toda compra lleva stop < cierre")

    diaria = [[T0 + d * 86400, 100, 101, 99, 100, 1] for d in range(10)]
    ok(estrategias.alinear((diaria, 1440), T0 + 86400 + 3600) == 0 and estrategias.alinear((diaria, 1440), T0 + 86400) == 0, "alinear: la última diaria cerrada")
    ok(estrategias.alinear((diaria, 1440), T0 + 3600) is None and estrategias.alinear((diaria, 1440), T0 + 5 * 86400 + 60) == 4, "alinear: None antes de la primera, nunca la del propio día")

    comb = estrategias.combinaciones({"a": [1, 2], "b": [3, 4]}, {"a": 1, "b": 3})
    ok(len(comb) == 4 and comb[0] == {"a": 1, "b": 3} and len({json.dumps(c, sort_keys=True) for c in comb}) == 4, "combinaciones: 4 con el defecto primero")
    ok(estrategias.combinaciones({}, {}) == [{}], "combinaciones vacías -> [{}]")
    ok(all(len(estrategias.combinaciones(e.rejilla, e.defecto)) <= 4 and all(e.defecto[k] in e.rejilla[k] for k in e.rejilla)
           for e in estrategias.REGISTRO.values()), "cada estrategia: <= 4 combinaciones y defecto dentro de la rejilla")
    ok([e["id"] for e in estrategias.lista()] == ["rotura_dia", "pico_volumen", "cruce_medias", "donchian", "bandas", "rsi"]
       and all(e["descripcion"] and e["marco"] for e in estrategias.lista()), "lista(): las seis estrategias con descripción")


# =====================================================================================================================
# 9.5 Backtest: estrategias
# =====================================================================================================================

def prueba_estrategias():
    pv = estrategias.REGISTRO["pico_volumen"]
    plana5 = serie_sintetica(30, 5, 21, "plana")
    r = backtest.simular(plana5, pv, pv.defecto, CFG)
    ok(r["operaciones"] == [], "pico_volumen: sin picos no hay operaciones")
    picos = (1000, 3000, 5000)
    for i in picos:
        v = plana5[i]
        v[4] = round(v[1] + 0.5, 6)
        v[2] = max(v[2], v[4])
        v[5] = 20.0
        # keep the series coherent: the next bar opens at the new close (a gap back down would put the entry
        # within stop_min_pct of the stop at the pico's low and the engine would rightly reject it as stop_alto)
        sig = plana5[i + 1]
        sig[1] = v[4]
        sig[2] = max(sig[2], sig[1])
        sig[3] = min(sig[3], sig[1])
    r = backtest.simular(plana5, pv, pv.defecto, CFG)
    ok([o["entrada_t"] for o in r["operaciones"]] == [plana5[i + 1][0] for i in picos], f"pico_volumen: entra en la barra siguiente a cada pico ({len(r['operaciones'])} op)")
    ctx = pv.preparar(plana5, pv.defecto, "utc")
    iguales = all((ctx["ratio"][i] is not None and ctx["ratio"][i] >= 3) == (mercado.pico_volumen(plana5[:i + 1], 3, 48) is not None) for i in range(len(plana5)))
    ok(iguales, "pico_volumen: misma definición que la alerta del vigía (mercado.pico_volumen)")

    rd = estrategias.REGISTRO["rotura_dia"]
    rot = serie_sintetica(60, 15, 9, "rotura")
    r = backtest.simular(rot, rd, rd.defecto, CFG)
    ops = r["operaciones"]
    dias = [dia_utc(o["entrada_t"]) for o in ops]
    ok(len(ops) >= 40 and len(dias) == len(set(dias)), f"rotura_dia: una operación por día como máximo ({len(ops)} en 60 días)")
    ok(all(12 * 3600 + 15 * 60 <= o["entrada_t"] % 86400 <= 13 * 3600 + 30 * 60 for o in ops), "rotura_dia: entradas entre las 12:15 y las 13:30 UTC")
    senales = [o for o in ops if o["motivo"] == "senal"]
    ok(len(senales) >= 30 and all(hora_utc(o["salida_t"]) == 22 for o in senales), "rotura_dia: salida por señal a las 22 h del día de entrada")
    ok(any(o["motivo"] == "stop" for o in ops) and all(o["motivo"] in ("senal", "stop", "fin") for o in ops), "rotura_dia: los días falsos salen por stop")
    ok(all(o["objetivo"] is None for o in ops), "rotura_dia: sin objetivo")

    dc = estrategias.REGISTRO["donchian"]
    velas = [barra(k, 100, 100.5, 99.5, 100, marco=240) for k in range(100)]
    velas.append(barra(100, 100, 105.5, 99.8, 105, marco=240))
    velas += [barra(k, 105, 105.5, 104.5, 105, marco=240) for k in range(101, 115)]
    velas.append(barra(115, 105, 105.2, 103, 103.5, marco=240))
    velas += [barra(k, 103.5, 104, 103, 103.5, marco=240) for k in range(116, 120)]
    r = backtest.simular(velas, dc, dc.defecto, CFG)
    op = r["operaciones"][0] if r["operaciones"] else None
    ok(op is not None and op["entrada_i"] == 101 and op["motivo"] == "stop" and cerca(op["salida"], 104.5 * 0.999, 1e-6), f"donchian: el canal inferior es nivel de stop ({op and (op['motivo'], op['salida'])})")

    for eid, dias in (("cruce_medias", 120), ("bandas", 60), ("rsi", 60)):
        est = estrategias.REGISTRO[eid]
        serie = serie_sintetica(dias, est.marco, 17, "plana")
        r = backtest.simular(serie, est, est.defecto, CFG)
        ok(len(r["operaciones"]) >= 1 and r["rechazos"].get("sin_stop", 0) == 0, f"{eid}: >= 1 operación y ninguna rechazada por sin_stop ({len(r['operaciones'])} op)")


# =====================================================================================================================
# 9.6 Backtest: veredicto
# =====================================================================================================================

def correr(*a, **kw):
    global CORRER_N
    res = backtest.correr(*a, avisar=lambda m: None, **kw)
    CORRER_N += 1
    return res


def prueba_veredicto():
    def cada_barra(i, velas, ctx, pos, params, memoria):
        if pos is None:
            return {"accion": "comprar", "stop": velas[i][4] - 2 * ctx["atr"][i]}
        if i - pos["entrada_i"] >= 1:
            return {"accion": "vender", "motivo": "tiempo"}
        return None

    limpiar_historico()
    plana = serie_sintetica(420, 15, 7, "plana")
    sembrar("XBTEUR", 15, plana)
    mala = estrategias.EstrategiaFija(cada_barra, marco=15, calentamiento=15, ctx=lambda velas: {"atr": estrategias.atr(velas, 14)})
    res = correr(mala, "XBTEUR", semillas=5, cfg=CFG)
    v = res["oos"]["veredicto"]
    ok(v["clave"] == "no_pasa" and v["texto"] == backtest.VEREDICTOS["no_pasa"], f"debe fallar: {v['texto']}")
    ok(any(m.startswith("Expectativa") for m in v["motivos"]) and any(m.startswith("Profit factor") for m in v["motivos"]), f"debe fallar: motivos Expectativa y Profit factor ({v['motivos']})")
    ok(res["oos"]["metricas"]["expectativa_R"] < 0, "debe fallar: expectativa negativa con los costes")
    ok(res["dia"] == "utc" and res["version"] == 1 and res["par"] == "XBTEUR" and res["marco"] == 15, "resultado: dia, version, par, marco")

    res = correr(estrategias.EstrategiaFija(lambda *a: None, marco=15), "XBTEUR", semillas=5, cfg=CFG)
    m, v = res["oos"]["metricas"], res["oos"]["veredicto"]
    ok(v["clave"] == "no_pasa" and m["n"] == 0 and m["profit_factor"] is None and m["sharpe_diario"] is None, "n = 0: no_pasa sin explotar")
    ok(res["referencias"]["azar"]["p_azar"] is None and any(x.startswith("Operaciones") for x in v["motivos"]), "n = 0: p_azar None y motivo Operaciones")

    limpiar_historico()
    rot = serie_sintetica(420, 15, 3, "rotura")
    sembrar("XBTEUR", 15, rot)
    res = correr("rotura_dia", "XBTEUR", semillas=40, cfg=CFG)
    m, p, v = res["oos"]["metricas"], res["oos"]["puertas"], res["oos"]["veredicto"]
    ok(len(res["ventanas"]) == 4 and all(v_["parametros"] for v_ in res["ventanas"]), "debe pasar: 4 ventanas con parámetros")
    ok(m["n"] >= 200, f"debe pasar: n >= 200 ({m['n']})")
    ok(m["expectativa_R"] > 0.5, f"debe pasar: expectativa_R > 0,5 ({m['expectativa_R']})")
    ok(m["profit_factor"] is not None and m["profit_factor"] > 1.3, f"debe pasar: profit factor > 1,3 ({m['profit_factor']})")
    ok(m["max_drawdown_pct"] < 20, f"debe pasar: drawdown < 20 % ({m['max_drawdown_pct']})")
    ok(cerca(res["referencias"]["azar"]["p_azar"], 1 / 41, 1e-4) and res["referencias"]["azar"]["semillas"] == 40, f"debe pasar: p azar = 1/41 ({res['referencias']['azar']['p_azar']})")
    ok(p["consistencia"]["ok"] and "4 de 4" in p["consistencia"]["texto"] and m["apagones"] == 0 and p["apagado"]["ok"], f"debe pasar: 4 ventanas de 4 con beneficio, sin apagados ({p['consistencia']['texto']})")
    ok(v["clave"] == "pasa" and v["motivos"] == [], f"debe pasar: {v['texto']} ({v['motivos']})")
    ok(list(p) == ["expectativa", "profit_factor", "drawdown", "operaciones", "azar", "consistencia", "apagado"] and all(set(x) >= {"ok", "valor", "umbral", "texto"} for x in p.values()), "puertas: las siete en orden con ok, valor, umbral y texto")
    ok("is_total" in res and res["is_total"]["n"] > 0 and "sharpe_diario" in m and "ic_expectativa_R" in m, "is_total y oos.metricas presentes")
    ok(any("Profit factor > 3" in a for a in res["oos"]["avisos"]) and any("365" in a for a in res["oos"]["avisos"]), f"avisos: profit factor > 3 y menos de 365 días ({res['oos']['avisos']})")
    ok("comprar_y_mantener" in res["referencias"] and "comprar_y_mantener_30" in res["referencias"] and len(res["curva"]) <= 600 and len(res["curva_bh"]) <= 600, "referencias y curvas submuestreadas")
    ok(res["aviso"] == backtest.AVISO_HONESTO and res["costes"]["comision_pct"] == 0.4 and res["tz"], "aviso honesto, costes y zona horaria en el resultado")
    ok(all(set(o) >= {"ventana", "entrada_t", "salida_t", "entrada", "salida", "stop", "cantidad", "pnl", "R", "motivo", "fecha_entrada"} for o in res["operaciones"]), "operaciones con los campos congelados")
    id_pasa = res["id"]

    limpiar_historico()
    sembrar("XBTEUR", 15, rot[:300 * 96])
    res = correr("rotura_dia", "XBTEUR", semillas=5, cfg=CFG)
    v = res["oos"]["veredicto"]
    ok(v["clave"] == "insuficiente" and v["texto"].startswith("INSUFICIENTE: hacen falta al menos 4 ventanas"), f"300 días: {v['texto']}")
    limpiar_historico()
    con_hueco = [b for b in rot if not (T0 + 200 * 86400 <= b[0] < T0 + 209 * 86400)]
    sembrar("XBTEUR", 15, con_hueco)
    res = correr("rotura_dia", "XBTEUR", semillas=5, cfg=CFG)
    v = res["oos"]["veredicto"]
    ok(v["clave"] == "insuficiente" and "hueco de 9 días" in v["texto"], f"hueco de 9 días: {v['texto']}")

    limpiar_historico()
    sembrar("XBTEUR", 15, rot)
    ahora = 1_791_225_000
    r1 = correr("rotura_dia", "XBTEUR", semillas=5, cfg=CFG, ahora=ahora)
    r2 = correr("rotura_dia", "XBTEUR", semillas=5, cfg=CFG, ahora=ahora + 60)
    ok(r1["oos"]["metricas"] == r2["oos"]["metricas"] and r1["referencias"]["azar"]["p_azar"] == r2["referencias"]["azar"]["p_azar"]
       and r1["operaciones"] == r2["operaciones"] and r1["ventanas"] == r2["ventanas"], "determinismo: dos correr iguales")
    ok(r1["id"] == time.strftime("%Y%m%d-%H%M%S", time.localtime(ahora)) + "-rotura_dia-XBTEUR" and backtest.RE_ID.fullmatch(r1["id"]), "id del resultado")

    cte = serie_sintetica(10, 60, 0, "constante")
    bh = backtest.comprar_y_mantener(cte, CFG, T0, T0 + 10 * 86400)
    ok(cerca(bh["metricas"]["rentabilidad_neta_pct"], -0.896, 0.01), f"comprar y mantener sobre constante: -0,896 % ({bh['metricas']['rentabilidad_neta_pct']})")
    ok(cerca(bh["al_30"]["rentabilidad_neta_pct"], -0.269, 0.01), f"al 30 %: -0,269 % ({bh['al_30']['rentabilidad_neta_pct']})")
    vuelta = [[T0, 100, 100, 100, 100, 1], [T0 + 3600, 100, 100, 50, 50, 1], [T0 + 7200, 50, 100, 50, 100, 1]]
    bh = backtest.comprar_y_mantener(vuelta, CFG, T0, T0 + 3 * 3600)
    ok(cerca(bh["al_30"]["max_drawdown_pct"], 14.95, 0.05) and cerca(bh["metricas"]["max_drawdown_pct"], 50.0, 0.1), f"al 30 %: MDD de la curva mixta 14,95 % ({bh['al_30']['max_drawdown_pct']})")

    plana = serie_sintetica(60, 15, 11, "plana")
    az = backtest.azar(plana, CFG, 50, 0.01, 10, T0, T0 + 60 * 86400, 20, 0.1)
    ok(az["semillas"] == 20 and 1 / 21 - 1e-9 <= az["p_azar"] <= 1.0, f"azar: 20 semillas, p en [1/21, 1] ({az['p_azar']})")
    ok(set(az["expectativa_R"]) == {"p5", "p50", "p95"} and az["expectativa_R"]["p5"] <= az["expectativa_R"]["p95"], "azar: percentiles")
    az = backtest.azar(plana, CFG, 50, 0.01, 10, T0, T0 + 60 * 86400, 20, float("inf"))
    ok(cerca(az["p_azar"], 1 / 21, 1e-6) and az["mejor_que_pct"] == 100.0, "azar: nadie gana a infinito -> p = 1/21")

    ind = backtest.indice(50)
    ok(ind and ind[0]["id"] == r2["id"] and (nucleo.DATOS_DIR / "backtests" / f"{r2['id']}.json").is_file(), "indice: el último primero y el fichero existe")
    ok(set(ind[0]) >= {"id", "fecha", "estrategia", "titulo", "par", "marco", "oos_desde", "oos_hasta", "ventanas", "n", "expectativa_R", "profit_factor", "max_drawdown_pct", "p_azar", "veredicto"}, "indice: resumen con las claves congeladas")
    ok(backtest.resultado("../x") is None and backtest.resultado("20260101-000000-rotura_dia-NADIE") is None, "resultado: ids raros -> None")
    ok(backtest.resultado(id_pasa)["version"] == 1 and backtest.resultado(id_pasa)["oos"]["veredicto"]["clave"] == "pasa", "resultado(id) lee el fichero")
    ok(backtest.pruebas_total() == CORRER_N, f"pruebas_total == {CORRER_N} correr ejecutados ({backtest.pruebas_total()})")
    ok(backtest.intentos_previos("rotura_dia", "XBTEUR") >= 4 and backtest.intentos_previos("rsi", "ETHEUR") == 0, "intentos_previos por estrategia y par")
    ok(r2["intentos_previos"] >= 1 and any("backtests de esta estrategia y par" in a for a in r2["oos"]["avisos"]), "aviso de intentos previos")
    d = mercado.diario(1)[0]
    vt = r2["oos"]["veredicto"]
    ok(d["tipo"] == "idea" and d["autor"] == "cuant" and d["texto"].startswith("Backtest") and (vt["texto"].split(":")[0] in d["texto"] or vt["clave"] in d["texto"]),
       "el diario tiene la entrada del backtest")
    ok(len(backtest.submuestrear(list(range(5000)), 600)) <= 601 and backtest.submuestrear([1, 2, 3], 600) == [1, 2, 3], "submuestrear")
    inf = nucleo.Informe()
    backtest.comprobar(inf)
    sec = next((s for s in inf.secciones if s["titulo"] == "Backtests"), None)
    ok(sec is not None and any("rotura" in (i["nombre"] + i["detalle"]).lower() or "BTC/EUR" in (i["nombre"] + i["detalle"]) for i in sec["items"]), "comprobar(inf) añade Backtests con el último")


# =====================================================================================================================
# 9.7 Panel: backtest
# =====================================================================================================================

class BotFalso:
    activo = True
    enviados = []

    def texto(self, mensaje):
        self.enviados.append(mensaje)
        return True

    def comprobar(self, inf):
        inf.seccion("Telegram")
        inf.ok("Telegram bot", "(falso)")


def prueba_panel():
    app = panel.crear_panel(vigia=False, bot=BotFalso())
    app.config["TESTING"] = True
    lanzados = []
    app.lanzar_backtest = lambda *a: lanzados.append(a)
    app.lanzar_historico = lambda: None
    app.lanzar_calendario = lambda: None
    c = app.test_client()
    BASE = "http://127.0.0.1:5100"
    csrf = app.config["CSRF_TOKEN"]
    ultimo = backtest.indice(1)[0] if backtest is not None and backtest.indice(1) else None

    r = c.get("/backtest", base_url=BASE)
    html = r.get_data(as_text=True)
    ok(r.status_code == 200 and "Simulación · datos públicos · sin dinero real" in html, "GET /backtest con aviso legal")
    ok(backtest is not None and backtest.AVISO_HONESTO in html and "la mayoría de quien hace trading a corto plazo pierde dinero" in html, "GET /backtest lleva el aviso honesto entero")
    ok(all(f'value="{e}"' in html for e in ("rotura_dia", "pico_volumen", "cruce_medias", "donchian", "bandas", "rsi")), "GET /backtest lista las seis estrategias")
    ok(ultimo is not None and ultimo["id"] in html and ultimo["veredicto"]["texto"] in html, "GET /backtest muestra el último backtest con su veredicto")
    ok("0,4" in html or "0.4" in html, "GET /backtest dice la comisión")
    ok(c.get("/", base_url=BASE).status_code == 200, "GET / sigue en 200")

    datos = {"estrategia": "rotura_dia", "par": "XBTEUR", "desde": "2025-01-01", "hasta": ""}
    ok(c.post("/backtest", base_url=BASE, data=datos).status_code == 403, "POST /backtest sin CSRF -> 403")
    ok(c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf}, headers={"Origin": "http://evil.example"}).status_code == 403, "POST /backtest con Origin ajeno -> 403")
    ok(c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf, "estrategia": "martingala"}).status_code == 404, "POST /backtest estrategia desconocida -> 404")
    ok(c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf, "par": "DOGEEUR"}).status_code == 404, "POST /backtest par fuera de config -> 404")
    r = c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf, "desde": "2025-13-01", "ajax": "1"})
    ok(r.status_code == 400 and r.json["ok"] is False and "Fecha" in r.json["error"], "POST /backtest fecha inválida -> 400 en español")
    r = c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf, "ajax": "1"})
    ok(r.status_code == 200 and r.json["ok"] is True and "trabajo" in r.json, "POST /backtest ajax -> ok")
    ok(lanzados and tuple(lanzados[-1]) == ("rotura_dia", "XBTEUR", "2025-01-01", ""), f"POST /backtest llama a lanzar_backtest ({lanzados[-1:] })")
    r = c.post("/backtest", base_url=BASE, data={**datos, "_csrf": csrf})
    ok(r.status_code == 302 and "/backtest" in r.headers.get("Location", ""), "POST /backtest sin ajax redirige a /backtest")
    r = c.post("/historico", base_url=BASE, data={"_csrf": csrf, "ajax": "1"})
    ok(r.status_code == 200 and r.json["ok"] is True, "POST /historico ajax -> ok")
    ok(c.post("/historico", base_url=BASE, data={"_csrf": csrf}).status_code == 302, "POST /historico sin ajax redirige")

    r = c.get("/backtest/estado", base_url=BASE)
    ok(r.status_code == 200 and set(r.json) >= {"trabajo", "trabajo_hist", "indice", "pruebas_total", "historico", "ocupado", "hora"}, "GET /backtest/estado con las claves")
    ok(r.json["ocupado"] is False and isinstance(r.json["indice"], list) and r.json["pruebas_total"] == CORRER_N, "GET /backtest/estado: ocupado False, índice y total")
    if ultimo:
        r = c.get(f"/backtest/{ultimo['id']}.json", base_url=BASE)
        ok(r.status_code == 200 and "curva" in r.json and "puertas" in r.json["oos"], "GET /backtest/<id>.json")
    ok(c.get("/backtest/20200101-000000-rotura_dia-XBTEUR.json", base_url=BASE).status_code == 404, "GET /backtest/<id inventado>.json -> 404")
    ok(c.get("/backtest/..%2Fx.json", base_url=BASE).status_code == 404, "GET /backtest/..%2Fx.json -> 404")
    ok(c.get("/backtest", base_url="http://evil.example:5100").status_code == 403, "Host ajeno -> 403")
    r = c.get("/comprobar", base_url=BASE)
    html = r.get_data(as_text=True)
    ok(r.status_code == 200 and "Histórico" in html and "Backtests" in html, "GET /comprobar con Histórico y Backtests")


# =====================================================================================================================
# 9.8 CLI
# =====================================================================================================================

def prueba_cli():
    import app as app_mod

    def main_con(argv):
        salida = io.StringIO()
        argv_antes = sys.argv
        sys.argv = ["app.py"] + argv
        try:
            with contextlib.redirect_stdout(salida):
                try:
                    codigo = app_mod.main()
                except SystemExit as e:
                    codigo = e.code
        finally:
            sys.argv = argv_antes
        return codigo, salida.getvalue()

    codigo, texto = main_con(["backtest", "--lista"])
    ok(codigo == 0 and all(e in texto for e in ("rotura_dia", "pico_volumen", "cruce_medias", "donchian", "bandas", "rsi")), "CLI: backtest --lista imprime las seis")
    codigo, texto = main_con(["backtest"])
    ok(codigo == 1, "CLI: backtest sin estrategia -> 1")
    codigo, texto = main_con(["historico", "validar"])
    ok(codigo == 0, "CLI: historico validar -> 0 sin red")
    global CORRER_N
    codigo, texto = main_con(["backtest", "rotura_dia", "--par", "XBTEUR", "--semillas", "5"])
    CORRER_N += 1
    ok(codigo in (0, 2, 3), f"CLI: backtest rotura_dia -> {codigo}")
    ok(any(v in texto for v in backtest.VEREDICTOS.values()) and backtest.AVISO_HONESTO in texto, "CLI: imprime el veredicto y el aviso honesto")
    ok("Guardado en" in texto, "CLI: dice dónde ha guardado el resultado")


# =====================================================================================================================

seccion("Histórico: disco y lector", prueba_historico_disco, ("sala.historico", historico))
seccion("Histórico: fuentes", prueba_historico_fuentes, ("sala.historico", historico))
seccion("Backtest: motor", prueba_motor, ("sala.estrategias", estrategias), ("sala.backtest", backtest))
seccion("Backtest: métricas y walk-forward", prueba_metricas_wf, ("sala.estrategias", estrategias), ("sala.backtest", backtest))
seccion("Backtest: estrategias", prueba_estrategias, ("sala.estrategias", estrategias), ("sala.backtest", backtest))
seccion("Backtest: veredicto", prueba_veredicto, ("sala.historico", historico), ("sala.estrategias", estrategias), ("sala.backtest", backtest))
seccion("Panel: backtest", prueba_panel, ("sala.historico", historico), ("sala.estrategias", estrategias), ("sala.backtest", backtest))
seccion("CLI", prueba_cli, ("sala.historico", historico), ("sala.estrategias", estrategias), ("sala.backtest", backtest))

print()
print(f"({time.time() - INICIO_PRUEBAS:.1f} s)")
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
