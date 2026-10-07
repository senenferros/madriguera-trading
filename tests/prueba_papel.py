"""The paper portfolio (Fase 2) without network: synthetic daily candles in a temporary history store, a scripted
strategy for exact checks (a stop loss and a winning exit), the four real strategies on a random walk (day by day
equals all at once), the state in datos/papel, the easy page, the office and the CLI.

    python tests/prueba_papel.py
"""
import contextlib
import io
import math
import os
import random
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-papel-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

import requests  # noqa: E402


def sin_red(*a, **k):
    raise requests.ConnectionError("sin red en las pruebas")


requests.get = requests.post = sin_red
requests.Session.request = sin_red

from sala import equipo, estrategias, historico, papel  # noqa: E402
import app as app_cli  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


T0 = 1_704_067_200   # 2024-01-01 00:00 UTC, a Monday


def dias_laborables(n, t0=T0):
    out, d = [], 0
    while len(out) < n:
        t = t0 + d * 86400
        d += 1
        if ((t // 86400) + 3) % 7 < 5:   # Monday..Friday
            out.append(t)
    return out


def plana(ts, precio=100.0):
    return [[t, precio, precio + 1, precio - 1, precio, 1000.0] for t in ts]


def limpiar():
    import shutil
    for sub in ("historico", "papel"):
        shutil.rmtree(nucleo.DATOS_DIR / sub, ignore_errors=True)


# ---------- 1. a scripted strategy: one stop loss, one winning exit ----------
print("Guion: una pérdida por stop y una ganancia")
limpiar()
TS = dias_laborables(400)
N0 = 300
ORDENES = {}   # (par, t) -> signal


def guion(par):
    def senal(i, velas, ctx, pos, params, memoria):
        return ORDENES.get((par, velas[i][0]))
    return senal


estrategias.REGISTRO["guion_spx"] = estrategias.EstrategiaFija(guion("SPX500"), marco=1440)
estrategias.REGISTRO["guion_aapl"] = estrategias.EstrategiaFija(guion("AAPLUSD"), marco=1440)
CARTERA_REAL = papel.CARTERA
papel.CARTERA = [{"par": "SPX500", "estrategia": "guion_spx"}, {"par": "AAPLUSD", "estrategia": "guion_aapl"}]

ok(papel.resumen()["empezada"] is False and "simulación" in papel.texto_cli(papel.resumen())[0].lower(), "sin estado: no ha empezado")
for par in ("SPX500", "AAPLUSD"):
    historico.guardar(par, 1440, plana(TS[:N0]), fuente="test")
r = papel.evaluar(hoy="2026-10-07", avisar=lambda m: None)
ok(r["empezada"] and r["saldo"] == 500 and not r["operaciones"] and not r["posiciones"], "primera evaluación: 500 € y nada operado sobre el pasado")
ok((nucleo.DATOS_DIR / "papel" / "estado.json").exists(), "estado guardado en datos/papel/estado.json")

# SPX: buy signal at the close of day N0, filled at the open of N0+1, stop 95; day N0+2 falls to 90 -> stop
ORDENES[("SPX500", TS[N0])] = {"accion": "comprar", "stop": 95.0, "objetivo": None}
spx = plana(TS[:N0 + 4])
spx[N0 + 2] = [TS[N0 + 2], 99.0, 99.5, 90.0, 91.0, 1000.0]
spx[N0 + 3] = [TS[N0 + 3], 91.0, 92.0, 90.0, 91.0, 1000.0]
# AAPL: buy at N0, sell signal at N0+2 after a rise to 110, filled at the open of N0+3
ORDENES[("AAPLUSD", TS[N0])] = {"accion": "comprar", "stop": 95.0, "objetivo": None}
ORDENES[("AAPLUSD", TS[N0 + 2])] = {"accion": "vender", "motivo": "senal"}
aapl = plana(TS[:N0 + 4])
aapl[N0 + 2] = [TS[N0 + 2], 100.0, 111.0, 99.5, 110.0, 1000.0]
aapl[N0 + 3] = [TS[N0 + 3], 110.0, 111.0, 109.0, 110.0, 1000.0]

# the start bar (N0-1) was the last one seen; N0 is new, so its signal fires. Two days first, then the rest
historico.guardar("SPX500", 1440, spx[:N0 + 2], fuente="test")
historico.guardar("AAPLUSD", 1440, aapl[:N0 + 2], fuente="test")
st_antes = papel.cargar()
r1 = papel.evaluar(hoy="2026-10-08", avisar=lambda m: None)
ok(sorted(p["nombre"] for p in r1["posiciones"]) == ["Apple", "S&P 500"] and not r1["operaciones"], f"compra ejecutada al open siguiente ({r1['posiciones']})")
historico.guardar("SPX500", 1440, spx, fuente="test")
historico.guardar("AAPLUSD", 1440, aapl, fuente="test")
r2 = papel.evaluar(hoy="2026-10-09", avisar=lambda m: None)
st = papel.cargar()
ops = r2["operaciones"]
perd = [o for o in ops if o["par"] == "SPX500"]
gan = [o for o in ops if o["par"] == "AAPLUSD"]
ok(len(perd) == 1 and perd[0]["pnl"] < 0 and perd[0]["motivo"] == "stop" and -1.01 <= perd[0]["R"] <= -0.99,
   f"S&P 500: pérdida por stop de -1 R ({perd[0]['pnl'] if perd else None} €, {perd[0]['R'] if perd else None} R)")
ok(perd and 4.9 <= -perd[0]["pnl"] <= 5.01, "riesgo del 1 %: la pérdida es unos 5 € de 500 €")
ok(len(gan) == 1 and gan[0]["pnl"] > 0 and gan[0]["motivo"] == "senal" and gan[0]["fecha_salida"] > gan[0]["fecha_entrada"], f"Apple: ganancia por señal ({gan[0]['pnl'] if gan else None} €)")
valor = gan[0]["cantidad"] * gan[0]["entrada"] if gan else 0
ok(0 < valor <= 0.3 * 500 + 0.01, f"tope del 30 % por activo ({valor:.2f} €)")
ok(all(o["stop"] < o["entrada"] for o in ops), "todas con stop por debajo de la entrada")
costes = sum(o["cantidad"] * (o["entrada"] + o["salida"]) * 0.001 for o in ops)
ok(math.isclose(r2["saldo"], 500 + sum(o["pnl"] for o in ops), abs_tol=0.02) and costes > 0, "saldo = 500 + resultados (costes descontados)")
ok(r2["ganadas"] == 1 and r2["perdidas"] == 1 and r2["operaciones"][0]["motivo_texto"], "historial con ganadas y perdidas")
r3 = papel.evaluar(hoy="2026-10-09", avisar=lambda m: None)
ok(papel.cargar()["operaciones"] == st["operaciones"] and r3["saldo"] == r2["saldo"], "evaluar dos veces el mismo día no cambia nada")
ok(papel.resumen(hoy="2026-11-06")["dias"] == 30 and r3["dias_plan"] == 91, "días en marcha frente a los 3 meses previstos")
texto = "\n".join(papel.texto_cli(r3))
ok("simulación" in texto and "perdidas" in texto and "S&P 500" in texto, "texto de la CLI")

# kill switch: a crash below -12 % closes everything and stops new entries
limpiar()
ORDENES.clear()
for par in ("SPX500", "AAPLUSD"):
    historico.guardar(par, 1440, plana(TS[:N0]), fuente="test")
papel.evaluar(hoy="2026-10-07", avisar=lambda m: None)
st = papel.cargar()
st["efectivo"] = 444.0   # pretend 55 € were lost already (peak 500): one more small loss trips -12 %
st["pico"] = 500.0
st["equity_prev"] = st["inicio_dia"] = 444.0
papel.guardar(st)
ORDENES[("SPX500", TS[N0])] = {"accion": "comprar", "stop": 95.0, "objetivo": None}
ORDENES[("SPX500", TS[N0 + 3])] = {"accion": "comprar", "stop": 95.0, "objetivo": None}
historico.guardar("SPX500", 1440, spx, fuente="test")
historico.guardar("AAPLUSD", 1440, plana(TS[:N0 + 4]), fuente="test")
r = papel.evaluar(hoy="2026-10-09", avisar=lambda m: None)
ok(r["apagado"] and not r["posiciones"] and len(r["operaciones"]) == 1, "apagado del −12 %: se para y no abre nada más")
ok("APAGADA" in "\n".join(papel.texto_cli(r)), "la CLI dice que está apagada")

# ---------- 2. the four real strategies on a random walk ----------
print("Las cuatro estrategias reales")
papel.CARTERA = CARTERA_REAL
ok([(x["par"], x["estrategia"]) for x in papel.CARTERA] == [("SPX500", "rebote_minimo"), ("NDX100", "rebote_minimo"),
                                                           ("DAX40EUR", "rebote_minimo"), ("AAPLUSD", "donchian_dia")], "la cartera: las 4 que dieron PASA")


def paseo(semilla, n):
    rng = random.Random(semilla)
    out, c = [], 100.0
    for k, t in enumerate(dias_laborables(n)):
        deriva = 0.002 * math.sin(2 * math.pi * k / 120)
        o = c * (1 + rng.gauss(0, 0.004))
        c = o * (1 + deriva + rng.gauss(0, 0.014))
        out.append([t, round(o, 4), round(max(o, c) * (1 + abs(rng.gauss(0, 0.005))), 4),
                    round(min(o, c) * (1 - abs(rng.gauss(0, 0.005))), 4), round(c, 4), 1000.0])
    return out


SERIES = {par: paseo(k + 11, 700) for k, par in enumerate(("SPX500", "NDX100", "DAX40EUR", "AAPLUSD"))}
INICIO, FIN = 400, 700


def correr(paso):
    limpiar()
    for par, s in SERIES.items():
        historico.guardar(par, 1440, s[:INICIO], fuente="test")
    papel.evaluar(hoy="2026-10-07", avisar=lambda m: None)
    n = INICIO
    while n < FIN:
        n = min(FIN, n + paso)
        for par, s in SERIES.items():
            historico.guardar(par, 1440, s[:n], fuente="test")
        papel.evaluar(hoy="2026-10-07", avisar=lambda m: None)
    return papel.cargar()


uno = correr(1)
todo = correr(FIN)
ok(len(uno["operaciones"]) > 3, f"hay operaciones en 300 días ({len(uno['operaciones'])})")
ok(uno["operaciones"] == todo["operaciones"] and math.isclose(uno["efectivo"], todo["efectivo"], abs_tol=1e-6),
   "día a día da lo mismo que todo de golpe")
ok(all(o["R"] >= -1.6 for o in uno["operaciones"]) and any(o["pnl"] < 0 for o in uno["operaciones"]), "las pérdidas, acotadas por el stop, y apuntadas")
ok({o["estrategia"] for o in uno["operaciones"]} <= {"rebote_minimo", "donchian_dia"}, "solo las estrategias de la cartera")
res = papel.resumen(uno)
ok(math.isclose(res["saldo"], res["efectivo"] + sum(p["valor"] for p in res["posiciones"]), abs_tol=0.05), "saldo = efectivo + posiciones")

# ---------- 3. panel, office, switch and CLI ----------
print("Panel, oficina y CLI")
ok("papel" in equipo.AUTOMATICOS, "interruptor «papel» en automático")


class BotFalso:
    activo = False

    def texto(self, m):
        return True

    def comprobar(self, inf):
        pass


app = panel.crear_panel(vigia=False, bot=BotFalso())
app.config["TESTING"] = True
c = app.test_client()
BASE = "http://127.0.0.1:5100"
ok(callable(getattr(app, "lanzar_papel", None)), "el panel sabe lanzar la cartera")
html = c.get("/", base_url=BASE).get_data(as_text=True)
perdida = next(o for o in res["operaciones"] if o["pnl"] < 0)
ok("Cartera de mentira (500 €)" in html and "simulación" in html and f"{perdida['pnl']:+.2f} €" in html and f"día {res['dias']} de los 91" in html,
   "portada: bloque con saldo, días y las pérdidas")
of = c.get("/oficina", base_url=BASE).get_data(as_text=True)
ok("Cartera de mentira (500 €)" in of and "Posiciones abiertas (simulación)" in of, "oficina: el bloque en la sala de bots")
o = c.get("/oficina/estado", base_url=BASE).json
bots = next(d for d in o["departamentos"] if d["id"] == "bots")
ok(any("Cartera de mentira" in p["titulo"] for p in bots["pantallas"]) and o["papel"]["empezada"], "oficina: pantallas de la cartera")
from sala import oficina  # noqa: E402
ok(any("no ha empezado" in p["valor"] for p in next(d for d in oficina.estado()["departamentos"] if d["id"] == "bots")["pantallas"]), "oficina sin cartera: «Todavía no ha empezado»")

salida = io.StringIO()
argv = sys.argv
sys.argv = ["app.py", "papel", "--sin-red"]
try:
    with contextlib.redirect_stdout(salida):
        codigo = app_cli.main()
finally:
    sys.argv = argv
ok(codigo == 0 and "Cartera de mentira" in salida.getvalue() and "simulacion" in salida.getvalue().lower(), "python app.py papel --sin-red")

print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
