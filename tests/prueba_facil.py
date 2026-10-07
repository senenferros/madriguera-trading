"""The easy front page without network: a fake Stooq feeds sala/bolsa.py, then the CSV parser, the 6-hour cache, a
network error that keeps the cache, every traffic-light color, the honest banner, the routes and the CLI.

    python tests/prueba_facil.py
"""
import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-facil-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

import requests  # noqa: E402

from sala import bolsa, historico  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


# ---------- fake series ----------

FIN = date(2026, 10, 2)


def serie(n, fn):
    """n daily closes ending on FIN; fn(i) is the close of day i (0 = oldest)."""
    return [[date.fromordinal(FIN.toordinal() - (n - 1 - i)).isoformat(), fn(i)] for i in range(n)]


def a_csv(filas):
    lineas = ["Date,Open,High,Low,Close,Volume"]
    lineas += [f"{d},{c:.4f},{c * 1.01:.4f},{c * 0.99:.4f},{c:.4f},1000" for d, c in filas]
    return "\n".join(lineas) + "\n"


SUBE = serie(400, lambda i: 100 * 1.001 ** i)                                     # calm steady rise
MOVIDA = serie(400, lambda i: 100 * 1.001 ** i * (1.04 if i % 2 else 0.97))        # rising but jumping ~5 % a day
DESPLOME = serie(400, lambda i: 100 * 1.002 ** min(i, 340) * 0.994 ** max(0, i - 340))   # 30 % down in 60 days
GOTEO = serie(400, lambda i: 100 * 0.9995 ** i)                                    # slow decline, < 20 % off the high
LADO = serie(400, lambda i: 100 + (3 if (i // 15) % 2 else -3))                    # flat, small steps

print("Stooq: CSV")
filas = bolsa.parsear_csv("﻿Date,Open,High,Low,Close,Volume\n2026-01-02,1,2,0.5,1.5,10\n2026-01-01,1,1,1,1.25,5\nmal,1,1,1,1,1\n2026-01-02,1,2,0.5,1.75,10\n")
ok(filas == [["2026-01-01", 1.25], ["2026-01-02", 1.75]], "parsear_csv ordena, deduplica (gana la última) y salta filas malas")
for basura in ("No data", "", "<html>error</html>"):
    try:
        bolsa.parsear_csv(basura)
        ok(False, f"parsear_csv rechaza {basura!r}")
    except ValueError:
        ok(True, f"parsear_csv rechaza {basura!r}")

print("Stooq: caché")
ok([m["clave"] for m in bolsa.mercados()] == ["sp500", "ibex35", "oro", "plata", "brent", "tesla"], "seis mercados por defecto")
ok(bolsa.mercados({"mercados_extra": [{"clave": "dax", "nombre": "DAX", "simbolo": "^dax"}, {"clave": "../x", "simbolo": "y"}]})
   == [{"clave": "dax", "nombre": "DAX", "simbolo": "^dax", "moneda": "USD", "tipo": "indice"}], "mercados_extra de config.yaml, con claves validadas")
SERIES = {"^spx": SUBE, "^ibex": LADO, "xauusd": SUBE, "xagusd": MOVIDA, "cb.f": GOTEO, "tsla.us": DESPLOME}
llamadas = []


def stooq_falso(simbolo):
    llamadas.append(simbolo)
    return a_csv(SERIES[simbolo])


bolsa.descargar_fn = stooq_falso
T = time.time()
res = bolsa.actualizar(avisar=lambda m: None, ahora=T)
ok(all(v == "nuevo" for v in res.values()) and len(llamadas) == 6, "primera descarga: seis mercados")
ok((nucleo.DATOS_DIR / "bolsa" / "oro.json").is_file() and len(bolsa.cache("oro")["filas"]) == 400, "caché en datos/bolsa/<clave>.json")
llamadas.clear()
res = bolsa.actualizar(avisar=lambda m: None, ahora=T + 3600)
ok(not llamadas and all(v == "reciente" for v in res.values()), "a la hora no vuelve a descargar (máximo cada 6 h)")


def sin_red(simbolo):
    raise requests.ConnectionError("proxy 403")


bolsa.descargar_fn = sin_red
res = bolsa.actualizar(avisar=lambda m: None, ahora=T + 7 * 3600)
ok(all(v.startswith("error") for v in res.values()), "sin red: error en cada mercado")
c = bolsa.cache("oro")
ok(len(c["filas"]) == 400 and c["descargado"] == T and c["error"], "sin red: la caché vieja se conserva")
oro = next(t for t in bolsa.tarjetas() if t["clave"] == "oro")
ok(oro["nota"].startswith("Sin datos nuevos desde") and oro["color"] != "gris", "sin red: la tarjeta dice «sin datos nuevos desde …» y mantiene el color")
bolsa.descargar_fn = stooq_falso
bolsa.actualizar(avisar=lambda m: None, forzar=True, ahora=T + 8 * 3600)
ok(not bolsa.cache("oro")["error"], "con red otra vez: error borrado")

print("Semáforo")
casos = {"verde": SUBE, "amarillo (se mueve mucho)": MOVIDA, "amarillo (de lado)": LADO, "rojo (-30 % del máximo)": DESPLOME,
         "rojo (bajo su media, que baja)": GOTEO}
luces = {k: bolsa.semaforo(v, "USD") for k, v in casos.items()}
for k, luz in luces.items():
    ok(luz["color"] == k.split()[0], f"{k} -> {luz['color']}: {luz['titular']}")
ok("mucho" in luces["amarillo (se mueve mucho)"]["titular"], "amarillo movido: lo dice en el titular")
ok(bolsa.semaforo(SUBE[:30])["color"] == "gris" and bolsa.semaforo([])["color"] == "gris", "pocos datos o ninguno -> gris")
v = luces["verde"]
ok(len(v["datos"]) == 3 and "hacia arriba" in v["datos"][2] and "Hoy +0,1 %" in v["datos"][1], "verde: precio, cambios y rumbo en palabras")
ok("hacia abajo" in luces["rojo (bajo su media, que baja)"]["datos"][2], "goteo: «va hacia abajo este año»")
ok("de lado" in luces["amarillo (se mueve mucho)"]["datos"][2] or "hacia" in luces["amarillo (se mueve mucho)"]["datos"][2], "movida: rumbo descrito")
ok(abs(bolsa.semaforo(DESPLOME)["numeros"]["desde_maximo_pct"] + 30.2) < 1, "distancia al máximo del año calculada")
prohibidas = ("compra", "vende", "vender", "comprar", "ganar", "oportunidad")
textos = " ".join(l["titular"] + " ".join(l["datos"]) for l in luces.values()).lower()
ok(not any(p in textos for p in prohibidas), "ningún semáforo dice comprar, vender o ganar")

print("Cripto (histórico de Kraken)")
hoy_t = int(time.time()) // 86400 * 86400
velas = [[hoy_t - (300 - i) * 86400, 0, 0, 0, 0, 0] for i in range(300)]
for i, v in enumerate(velas):
    p = 50000 * 1.001 ** i
    v[1:6] = [p, p * 1.001, p * 0.999, p, 10.0]
historico.guardar("XBTEUR", 1440, velas, fuente="test")
btc = next(t for t in bolsa.tarjetas() if t["clave"] == "btc")
ok(btc["color"] == "verde" and "€" in btc["datos"][0], "BTC desde el histórico diario: verde, en euros")
eth = next(t for t in bolsa.tarjetas() if t["clave"] == "eth")
ok(eth["color"] == "gris" and eth["nota"], "ETH sin histórico: gris con nota")

print("Banner")
b = bolsa.banner([])
ok(not b["hay"] and "No." in b["titulo"] and "ninguna estrategia ha aprobado el examen todavía" in b["texto"], "sin resultados: «No»")
ind = [{"estrategia": "rotura_dia", "par": "XBTEUR", "titulo": "Rotura del día", "nombre_par": "BTC/EUR", "veredicto": {"clave": "no_pasa"}},
       {"estrategia": "rotura_dia", "par": "XBTEUR", "titulo": "Rotura del día", "nombre_par": "BTC/EUR", "veredicto": {"clave": "pasa"}}]
ok(not bolsa.banner(ind)["hay"], "un «pasa» antiguo no cuenta: manda el último por estrategia y par")
ind.insert(0, {"estrategia": "cruce_medias", "par": "ETHEUR", "titulo": "Cruce de medias", "nombre_par": "ETH/EUR", "veredicto": {"clave": "pasa"}})
b = bolsa.banner(ind)
ok(b["hay"] and "Cruce de medias" in b["texto"] and "ETH/EUR" in b["texto"] and "dinero ficticio" in b["texto"], "con un «pasa»: dice cuál y que solo sería paper trading")

print("Panel")


class BotFalso:
    activo = False

    def texto(self, m):
        return True

    def comprobar(self, inf):
        inf.seccion("Telegram")


app = panel.crear_panel(vigia=False, bot=BotFalso())
app.config["TESTING"] = True
lanzados = []
app.lanzar_bolsa = lambda: lanzados.append(1)
c = app.test_client()
BASE = "http://127.0.0.1:5100"
r = c.get("/", base_url=BASE)
html = r.get_data(as_text=True)
ok(r.status_code == 200, "GET / -> 200")
nombres = ["S&amp;P 500", "IBEX 35", "Oro", "Plata", "Petróleo Brent", "Tesla", "Bitcoin (BTC)", "Ethereum (ETH)"]
ok(all(n in html for n in nombres), "GET / muestra los seis mercados y BTC/ETH")
ok("¿Hay algo que hacer hoy? No." in html and "Modo experto" in html and "¿Qué significa?" in html, "GET / con banner, «Modo experto» y «¿Qué significa?»")
ok(html.count('class="luz ') >= 12 and "20 %" in html and "2,5 %" in html, "GET / con luces y los umbrales explicados")
r = c.get("/sala", base_url=BASE)
ok(r.status_code == 200 and "BTC/EUR" in r.get_data(as_text=True), "GET /sala (modo experto) sigue en 200")
for ruta in ("/oficina", "/backtest", "/comprobar"):
    ok(c.get(ruta, base_url=BASE).status_code == 200, f"GET {ruta} -> 200")
csrf = app.config["CSRF_TOKEN"]
ok(c.post("/bolsa", base_url=BASE, data={}).status_code == 403, "POST /bolsa sin CSRF -> 403")
r = c.post("/bolsa", base_url=BASE, data={"_csrf": csrf, "ajax": "1"})
ok(r.status_code == 200 and r.json["ok"] and lanzados, "POST /bolsa ajax lanza la actualización")
r = c.post("/bolsa", base_url=BASE, data={"_csrf": csrf})
ok(r.status_code == 302 and r.headers["Location"].endswith("/"), "POST /bolsa sin ajax vuelve a la portada")
ok(c.get("/facil/estado", base_url=BASE).status_code == 200, "GET /facil/estado")
ok(c.post("/diario", base_url=BASE, data={"_csrf": csrf, "tipo": "nota", "texto": ""}).headers["Location"].split("#")[0].endswith("/sala"),
   "POST /diario vuelve a /sala")
# a "pasa" in the real index reaches the page
from sala import backtest  # noqa: E402
indice_real = backtest.indice
backtest.indice = lambda n=50: ind
ok("Cruce de medias" in c.get("/", base_url=BASE).get_data(as_text=True), "GET / con un «pasa» en el índice lo nombra")
backtest.indice = indice_real
# the real background job, with the fakes
app2 = panel.crear_panel(vigia=False, bot=BotFalso())
historico_actualizar = historico.actualizar
historico.actualizar = lambda **kw: {}
ok(app2.lanzar_bolsa() is True, "lanzar_bolsa real arranca")
for _ in range(100):
    est = app2.test_client().get("/facil/estado", base_url=BASE).json["trabajo"]
    if est["estado"] != "corriendo":
        break
    time.sleep(0.05)
ok(est["estado"] == "ok", "lanzar_bolsa real termina en ok")
historico.actualizar = historico_actualizar

print("CLI")
import app as app_cli  # noqa: E402
salida = io.StringIO()
historico.actualizar = lambda **kw: {}
with contextlib.redirect_stdout(salida):
    codigo = app_cli.cmd_bolsa(argparse.Namespace(sin_red=False))
historico.actualizar = historico_actualizar
texto = salida.getvalue()
ok(codigo == 0 and "[VERDE" in texto and "[ROJO" in texto and "Tesla" in texto, "cmd_bolsa: código 0 e imprime las luces")
ok(texto.isascii(), "cmd_bolsa: solo ASCII")
p = subprocess.run([sys.executable, str(RAIZ / "app.py"), "bolsa", "--sin-red"], capture_output=True, text=True, cwd=str(TMP), timeout=60)
ok(p.returncode == 0 and "S&P 500" in p.stdout, "python app.py bolsa --sin-red: exit 0")

print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
