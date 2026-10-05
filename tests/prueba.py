"""Smoke test without network: a fake exchange feeds the watcher, then alerts, the journal number parser, the
calendar validation and the panel routes are checked with Flask's test client.

    python tests/prueba.py
"""
import json
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

from sala import equipo, mercado  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


# ---------- fake exchange: a flat day, then a breakout with a volume spike on the last closed candle ----------

AHORA = time.time()
INICIO = mercado._inicio_dia(AHORA)
# 200 one-minute candles ending with the candle that closed right before `ahora`
T0 = int(AHORA // 60 * 60) - 200 * 60
VELAS = {}


def velas_planas(precio):
    out = []
    for i in range(201):
        t = T0 + i * 60
        out.append([t, precio, precio + 5, precio - 5, precio, 1.0])
    return out


VELAS["XBTEUR"] = velas_planas(60000.0)
VELAS["ETHEUR"] = velas_planas(3000.0)
# the last closed candle of BTC closes above the day's high with 10x volume
VELAS["XBTEUR"][-2] = [T0 + 199 * 60, 60000.0, 60100.0, 59995.0, 60050.0, 10.0]


def exchange_falso(par, intervalo=1, desde=None):
    if par not in VELAS:
        raise RuntimeError(f"Kraken: sin datos para {par}")
    return [v for v in VELAS[par] if not desde or v[0] >= desde]


mercado.descargar_fn = exchange_falso
mercado.ALERTA_MAX_EDAD = 10 ** 9   # the fake candles may be "old" if the day started a moment ago; ignore the age filter here

print("Vigía")
avisos = []
# first pass: the whole day is new, the only pending candle is the last closed one (the breakout)
alertas = mercado.vigilar(avisos.append, ahora=AHORA)
tipos = sorted(a["tipo"] for a in alertas)
ok("rotura_max" in tipos, "rotura del máximo detectada")
ok("volumen" in tipos, "pico de volumen detectado")
ok(all(a["par"] == "XBTEUR" for a in alertas), "las alertas son de BTC")
est = mercado.estado()
ok(est["pares"]["XBTEUR"]["precio"] == 60000.0, "precio en el estado")
ok(est["pares"]["ETHEUR"]["precio"] == 3000.0, "segundo par en el estado")
ok(est.get("actualizado") == AHORA and not est.get("sin_conexion"), "estado actualizado y conectado")
ok(len(mercado.velas_dia("XBTEUR", mercado.dia_local(AHORA))) > 0 or len(mercado.velas_dia("XBTEUR", mercado.dia_local(T0))) > 0, "velas guardadas en disco")
ok(len(mercado.alertas()) == len(alertas) and len(alertas) == 2, "alertas anotadas en alertas.json")
ok(any(l.startswith("Vigía:") for l in avisos), "el vigía avisa por el log")
# second pass a minute later: nothing new closed, so no alert repeats (each side once a day, volume in cooldown)
alertas2 = mercado.vigilar(avisos.append, ahora=AHORA + 60)
ok(alertas2 == [], "sin alertas repetidas en la segunda pasada")
# the exchange goes down: no exception, last prices stay, sin_conexion set
mercado.descargar_fn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Kraken: HTTP 503"))
alertas3 = mercado.vigilar(avisos.append, ahora=AHORA + 120)
est = mercado.estado()
ok(alertas3 == [] and est["sin_conexion"] and est["pares"]["XBTEUR"]["precio"] == 60000.0, "sin conexión: precios anteriores se conservan")
ok(est["actualizado"] == AHORA + 60, "sin conexión: 'actualizado' no avanza")
mercado.descargar_fn = exchange_falso

print("Alertas (funciones puras)")
cerradas = [[i * 60, 100, 101, 99, 100, 1.0] for i in range(60)]
ok(mercado.rotura_dia(cerradas, [60 * 60, 100, 103, 99, 102, 1.0]) == "max", "rotura_dia max")
ok(mercado.rotura_dia(cerradas, [60 * 60, 100, 101, 97, 98, 1.0]) == "min", "rotura_dia min")
ok(mercado.rotura_dia(cerradas[:10], [40 * 60, 100, 103, 99, 102, 1.0]) is None, "rotura_dia necesita velas mínimas")
ok(mercado.rotura_dia(cerradas[:10], [40 * 60, 100, 103, 99, 102, 1.0], manana={"minutos": 60, "maximo": 101, "minimo": 99}) == "max", "rotura_dia con la mañana rellenada")
ok(mercado.pico_volumen(cerradas[:60] + [[60 * 60, 100, 101, 99, 100, 5.0]]) == 5.0, "pico_volumen x5")
ok(mercado.pico_volumen(cerradas[:60] + [[60 * 60, 100, 101, 99, 100, 2.0]]) is None, "pico_volumen por debajo del factor")
ok(mercado.fmt_precio(58230.5) == "58.230 €" and mercado.fmt_precio(0.5) == "0,50 €", "fmt_precio")
ok(mercado.nombre_par("XBTEUR") == "BTC/EUR", "nombre_par")

print("Parte")
texto = mercado.parte()
ok("BTC/EUR" in texto and "simulación" in texto.lower(), "el parte lleva precios y aviso de simulación")
ok(not mercado.toca_parte(ahora=time.mktime(time.strptime(f"{date.today().isoformat()} 07:59", "%Y-%m-%d %H:%M"))), "toca_parte antes de la hora: no")
ok(mercado.toca_parte(ahora=time.mktime(time.strptime(f"{date.today().isoformat()} 08:00", "%Y-%m-%d %H:%M"))), "toca_parte a la hora: sí")
mercado.anotar_parte_enviado()
ok(not mercado.toca_parte(ahora=time.mktime(time.strptime(f"{date.today().isoformat()} 09:00", "%Y-%m-%d %H:%M"))), "toca_parte tras anotar el envío: no")

print("Diario: números")
ok(mercado.numero("58.230,5") == 58230.5, "58.230,5")
ok(mercado.numero("58230.5") == 58230.5, "58230.5")
ok(mercado.numero("0,01") == 0.01 and mercado.numero("0.01") == 0.01, "0,01 y 0.01")
ok(mercado.numero("1.234.567") == 1234567.0, "1.234.567")
ok(mercado.numero("") is None and mercado.numero("  ") is None, "vacío -> None")
for malo in ("abc", "-5", "0", "nan", "inf"):
    try:
        mercado.numero(malo)
        ok(False, f"'{malo}' rechazado")
    except ValueError:
        ok(True, f"'{malo}' rechazado")
e = mercado.anotar_diario("compra_sim", "Prueba", "XBTEUR", 60000.0, 0.01)
ok(e["tipo"] == "compra_sim" and mercado.diario()[0]["texto"] == "Prueba", "anotar_diario")
try:
    mercado.anotar_diario("nota", "   ")
    ok(False, "texto vacío rechazado")
except ValueError:
    ok(True, "texto vacío rechazado")

print("Calendario")
ok(mercado.evento_valido({"fecha": "2026-13-01", "evento": "x"}) is None, "fecha imposible rechazada")
ok(mercado.evento_valido({"fecha": "2026-10-28", "evento": ""}) is None, "evento vacío rechazado")
v = mercado.evento_valido({"fecha": "2026-10-28", "hora": "19:00", "evento": "  FOMC   decisión ", "zona": "Marte", "importancia": "x", "fuente": "javascript:alert(1)"})
ok(v == {"fecha": "2026-10-28", "hora": "19:00", "evento": "FOMC decisión", "zona": "Otro", "importancia": "media", "fuente": ""}, "evento normalizado (zona, importancia, fuente)")
ok(mercado.evento_valido({"fecha": "2026-10-28", "hora": "25:99x", "evento": "x"})["hora"] == "", "hora mal formada -> vacía")
cal = mercado.calendario()
ok(len(cal["eventos"]) == 4 and cal["eventos"][0]["fecha"] == "2026-10-28", "semilla cargada y ordenada")
ok(len(mercado.proximos(400, desde=date(2026, 10, 1))) == 4 and mercado.proximos(5, desde=date(2026, 1, 1)) == [], "proximos filtra por fecha")


def claude_falso(prompt, schema, herramientas=None, timeout=0):
    hoy = date.today().isoformat()
    return {"eventos": [
        {"fecha": hoy, "hora": "14:30", "evento": "IPC de EEUU", "zona": "EEUU", "importancia": "alta", "fuente": "https://www.bls.gov/cpi/"},
        {"fecha": "2026-10-28", "hora": "19:00", "evento": "Decisión de tipos de la Reserva Federal (FOMC, 27-28 oct)", "zona": "EEUU", "importancia": "alta", "fuente": "https://x"},
        {"fecha": "no-fecha", "hora": "", "evento": "Basura", "zona": "Otro", "importancia": "baja", "fuente": ""},
    ], "notas": "prueba"}, 0.01


nuevos = mercado.actualizar_calendario(avisos.append, preguntar=claude_falso)
ok(len(nuevos) == 1 and nuevos[0]["evento"] == "IPC de EEUU", "actualizar_calendario: solo lo nuevo y válido")
ok(len(mercado.calendario()["eventos"]) == 5, "calendario.json fusionado con la semilla")
ok(json.loads((nucleo.DATOS_DIR / "calendario.json").read_text(encoding="utf-8"))["notas"] == "prueba", "calendario.json escrito")
sin_claude = []
import sala.claude as claude_mod
original = claude_mod.disponible
claude_mod.disponible = lambda: False
ok(mercado.actualizar_calendario(sin_claude.append) == [] and any("claude" in l.lower() for l in sin_claude), "sin el comando claude: lo dice en el log")
claude_mod.disponible = original

print("Equipo")
eq = equipo.equipo()
ok([m["rol"] for m in eq] == [r[0] for r in equipo.ROLES] and len(eq) == 7, "siete roles")
ok(len({m["nombre"] for m in eq}) == 7, "nombres sin repetir")
ok(eq == equipo.equipo() and (nucleo.DATOS_DIR / "equipo.yaml").is_file(), "equipo estable y guardado en equipo.yaml")
ok(next(m for m in eq if m["rol"] == "vigia")["automatico"] == "velas", "el vigía lleva el interruptor de velas")

print("Panel")


class BotFalso:
    activo = True
    enviados = []

    def texto(self, mensaje):
        self.enviados.append(mensaje)
        return True

    def comprobar(self, inf):
        inf.seccion("Telegram")
        inf.ok("Telegram bot", "(falso)")


bot = BotFalso()
app = panel.crear_panel(vigia=False, bot=bot)
app.lanzar_calendario = lambda: None
app.config["TESTING"] = True
c = app.test_client()
BASE = "http://127.0.0.1:5100"
csrf = app.config["CSRF_TOKEN"]
r = c.get("/", base_url=BASE)
ok(r.status_code == 200 and "Simulación · datos públicos · sin dinero real" in r.get_data(as_text=True), "GET / con aviso legal")
html = r.get_data(as_text=True)
ok("BTC/EUR" in html and "Reglas" in html or "reglas" in html, "GET / muestra pares y reglas")
ok("YouTube" not in html and "Shorts" not in html, "sin rastro de YouTube")
r = c.get("/oficina", base_url=BASE)
ok(r.status_code == 200 and "<canvas" in r.get_data(as_text=True) and all(m["nombre"] in r.get_data(as_text=True) for m in eq), "GET /oficina con canvas y equipo")
r = c.get("/estado", base_url=BASE)
ok(r.status_code == 200 and r.json["pares"][0]["par"] == "XBTEUR" and "automatico" in r.json, "GET /estado JSON")
ok(c.get("/", base_url="http://evil.example:5100").status_code == 403, "Host ajeno -> 403")
ok(c.post("/diario", base_url=BASE, data={"tipo": "nota", "texto": "x"}).status_code == 403, "POST sin CSRF -> 403")
ok(c.post("/diario", base_url=BASE, data={"_csrf": csrf, "tipo": "nota", "texto": "x"}, headers={"Origin": "http://evil.example"}).status_code == 403, "POST con Origin ajeno -> 403")
r = c.post("/diario", base_url=BASE, data={"_csrf": csrf, "tipo": "venta_sim", "texto": "Venta de prueba", "par": "ETHEUR", "precio": "3.100,5", "cantidad": "0,5", "ajax": "1"})
ok(r.status_code == 200 and r.json["ok"] and r.json["diario"][0]["precio"] == 3100.5 and r.json["diario"][0]["cantidad"] == 0.5, "POST /diario ajax")
r = c.post("/diario", base_url=BASE, data={"_csrf": csrf, "tipo": "nota", "texto": "x", "precio": "abc", "ajax": "1"})
ok(r.status_code == 400 and "Precio" in r.json["error"], "POST /diario precio inválido -> 400")
r = c.post("/diario", base_url=BASE, data={"_csrf": csrf, "tipo": "nota", "texto": ""})
ok(r.status_code == 302, "POST /diario sin ajax redirige")
r = c.post("/automatico", base_url=BASE, data={"_csrf": csrf, "tarea": "alertas", "activo": "0"})
ok(r.status_code == 200 and r.json["activo"] is False and nucleo.cargar_config()["automatico"]["alertas"] is False, "POST /automatico apaga y guarda en config.yaml")
r = c.post("/automatico", base_url=BASE, data={"_csrf": csrf, "tarea": "alertas", "activo": "1"})
ok(r.json["activo"] is True, "POST /automatico enciende")
ok(c.post("/automatico", base_url=BASE, data={"_csrf": csrf, "tarea": "youtube", "activo": "1"}).status_code == 404, "POST /automatico tarea desconocida -> 404")
antes = mercado.estado().get("actualizado")
r = c.post("/parte", base_url=BASE, data={"_csrf": csrf})
ok(r.status_code == 200 and "BTC/EUR" in r.json["texto"] and r.json["enviado"] is None, "POST /parte devuelve el texto")
ok(mercado.estado().get("actualizado") == antes, "POST /parte no hace una pasada del vigía")
r = c.post("/parte", base_url=BASE, data={"_csrf": csrf, "enviar": "1"})
ok(r.json["enviado"] is True and bot.enviados and "Parte" in bot.enviados[-1], "POST /parte enviar=1 usa el bot")
r = c.post("/calendario", base_url=BASE, data={"_csrf": csrf})
ok(r.status_code == 302, "POST /calendario redirige")
r = c.get("/comprobar", base_url=BASE)
ok(r.status_code == 200 and "Telegram" in r.get_data(as_text=True), "GET /comprobar")

print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
