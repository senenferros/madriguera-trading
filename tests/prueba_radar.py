"""The news radar without network or Claude: a fake preguntar plays the radar, then the old-news labels, the five-role
verdicts, the advice filter, the paper diary (approval, following, closing, maths, lessons), the page and the CLI.

    python tests/prueba_radar.py
"""
import argparse
import contextlib
import io
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-radar-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

from sala import analista, bolsa, mercado, radar  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


HOY = date(2026, 10, 7)
AHORA = datetime(2026, 10, 7, 9, 0).timestamp()


def dia(n):
    return date.fromordinal(HOY.toordinal() + n).isoformat()


def guardar_cierres(clave, filas):
    mercado._escribir_json(bolsa._ruta(clave), {"filas": filas, "descargado": time.time(), "intento": time.time(), "error": ""})


guardar_cierres("oro", [[dia(-2), 100.0], [dia(-1), 100.0]])
guardar_cierres("tesla", [[dia(-1), 200.0]])

print("Configuración y mercados")
conf = radar.configuracion({})
ok(conf == {"activo": True, "hora": "08:30"}, "radar activo a las 08:30 por defecto")
ok(radar.configuracion({"radar": {"activo": False, "hora": "9:15"}}) == {"activo": False, "hora": "09:15"}, "radar apagado y hora normalizada")
claves = [m["clave"] for m in radar.mercados()]
ok("oro" in claves and "btc" in claves and "eth" in claves, "mercados_extra más BTC y ETH")
ok(nucleo.automatico(nucleo.cargar_config(), "radar"), "interruptor «radar» encendido por defecto")

print("Detector de noticias viejas")
ok(radar.normalizar("¡El Oro SUBE un 2%!") == "oro sube 2", "normalizar quita tildes, signos y palabras vacías")
previas = [{"titulo": "La Fed mantiene los tipos de interés sin cambios", "url": "https://www.ejemplo.com/fed", "fecha_radar": dia(-2)}]
e = lambda t, u, f: radar.etiquetar({"titulo": t, "url": u, "fecha": f}, previas, HOY)[0]
ok(e("El oro marca un máximo histórico", "https://a.com/oro", dia(0)) == "NUEVA", "noticia distinta -> NUEVA")
ok(e("Otro titular", "http://ejemplo.com/fed/", dia(0)) == "DUPLICADA", "misma URL (sin www, http, barra) -> DUPLICADA")
ok(e("La Fed mantiene los tipos de interés sin cambio", "https://b.com/x", dia(0)) == "DUPLICADA", "título casi igual -> DUPLICADA")
ok(e("La Fed mantiene los tipos y avisa de recortes en diciembre", "https://b.com/y", dia(0)) == "ACTUALIZACIÓN", "misma historia con algo nuevo -> ACTUALIZACIÓN")
ok(e("El oro marca un máximo histórico", "https://a.com/oro2", dia(-4)) == "POSIBLEMENTE RECICLADA", "más de 3 días -> POSIBLEMENTE RECICLADA")
ok(e("El oro marca un máximo histórico", "https://a.com/oro3", dia(-3)) == "NUEVA", "justo 3 días todavía vale")
ok(e("El oro marca un máximo histórico", "https://a.com/oro4", "ayer") == "POSIBLEMENTE RECICLADA", "sin fecha clara -> POSIBLEMENTE RECICLADA")

# 14-day history on disk: one file inside the window, one outside
mercado._escribir_json(radar._carpeta() / f"{dia(-2)}.json", {"noticias": [previas[0] | {"fecha": dia(-2)}], "ideas": []})
mercado._escribir_json(radar._carpeta() / f"{dia(-20)}.json", {"noticias": [{"titulo": "Noticia muy vieja de Tesla", "url": "https://v.com/1"}]})
h = radar.historial(HOY)
ok(len(h) == 1 and h[0]["fecha_radar"] == dia(-2), "historial: solo los últimos 14 días")

print("Llamada falsa al radar")
llamadas = []


def respuesta():
    return {
        "noticias": [
            {"titulo": "El oro marca un máximo histórico", "url": "https://a.com/oro", "fecha": dia(0), "mercados": ["oro", "inventado"],
             "resumen": "El oro sube por la búsqueda de refugio. Hay que comprar ya."},
            {"titulo": "La Fed mantiene los tipos de interés sin cambios", "url": "https://otra.com/fed", "fecha": dia(0), "mercados": ["sp500"],
             "resumen": "La Fed no toca los tipos."},
            {"titulo": "Tesla entrega más coches de lo esperado", "url": "https://t.com/1", "fecha": dia(-6), "mercados": ["tesla"],
             "resumen": "Tesla entregó más coches."},
            {"titulo": "Sin fuente", "url": "no-es-url", "fecha": dia(0), "mercados": [], "resumen": "x"},
            {"titulo": "Del futuro", "url": "https://f.com", "fecha": dia(5), "mercados": [], "resumen": "x"},
        ],
        "ideas": [
            {"noticia": 0, "mercado": "oro", "escenario": "sube", "dias": 2, "veredicto": "ENSEÑAR AL HUMANO",
             "roles": {"scout": "El oro está en máximos.", "esceptico": "Puede ser ruido.", "cuant": "Se mueve un 1 % al día.",
                       "riesgo": "Se podrían perder 5 $ de mentira.", "revisor": "Sirve para aprender. Hay que vender mañana."}},
            {"noticia": 1, "mercado": "sp500", "escenario": "baja", "dias": 3, "veredicto": "ENSEÑAR AL HUMANO",
             "roles": {r: "Línea." for r in radar.ROLES}},
            {"noticia": 2, "mercado": "tesla", "escenario": "sube", "dias": 50, "veredicto": "VIGILAR",
             "roles": {r: "Línea." for r in radar.ROLES}},
            {"noticia": 1, "mercado": "nada", "escenario": "sube", "dias": 1, "veredicto": "VIGILAR", "roles": {}},
        ],
        "lecciones": [],
    }


respuestas = [respuesta()]


def preguntar(prompt, schema, herramientas, timeout):
    llamadas.append({"prompt": prompt, "schema": schema, "herramientas": herramientas})
    return respuestas.pop(0), 0.05


radar.preguntar_fn = preguntar
log = []
datos = radar.hacer(avisar=log.append, cfg=None, ahora=AHORA)
ok(len(llamadas) == 1 and llamadas[0]["herramientas"] == ["WebSearch"], "una sola llamada, con WebSearch")
ok(set(llamadas[0]["schema"]["properties"]) == {"noticias", "ideas", "lecciones"}, "esquema JSON con noticias, ideas y lecciones")
ok("SCOUT" in llamadas[0]["prompt"].upper() and "La Fed mantiene" in llamadas[0]["prompt"], "el prompt lleva los papeles y las noticias ya vistas")
ns = datos["noticias"]
ok(len(ns) == 3, "noticias sin URL o del futuro quitadas")
ok([n["etiqueta"] for n in ns] == ["NUEVA", "DUPLICADA", "POSIBLEMENTE RECICLADA"], "etiquetas: nueva, duplicada (14 días), reciclada (fecha)")
ok(ns[0]["mercados"] == ["oro"], "mercados desconocidos quitados")
ok("comprar" not in ns[0]["resumen"] and analista.FRASE_QUITADA in ns[0]["resumen"], "filtro de consejos en el resumen")
ids = datos["ideas"]
ok(len(ids) == 3, "idea con mercado desconocido quitada")
ok(ids[0]["veredicto"] == "ENSEÑAR AL HUMANO" and "vender" not in ids[0]["roles"]["revisor"] and ids[0]["corregido"], "filtro de consejos en los papeles")
ok(ids[1]["veredicto"] == "RECHAZAR" and "duplicada" in ids[1]["corregido"], "noticia duplicada -> RECHAZAR aunque la IA diga otra cosa")
ok(ids[2]["veredicto"] == "RECHAZAR" and ids[2]["dias"] == radar.DIAS_IDEA_MAX, "noticia reciclada -> RECHAZAR; días recortados a 10")
ok((radar._carpeta() / f"{dia(0)}.json").is_file() and radar.ultimo()["fecha"] == dia(0), "guardado en datos/radar/")
ok(not radar.toca(ahora=AHORA) and radar.toca(ahora=AHORA + 86400), "toca: una vez al día")
ok(not radar.toca(ahora=AHORA + 86400, cfg={"radar": {"activo": False}}), "toca: nunca con el radar apagado")

print("Propuestas y aprobación")
d = radar.cargar_diario()
ok(len(d["propuestas"]) == 1 and d["propuestas"][0]["mercado"] == "oro" and d["propuestas"][0]["estado"] == "pendiente",
   "solo ENSEÑAR AL HUMANO se vuelve propuesta, y pendiente")
ok(d["operaciones"] == [] and radar.saldo(d)["total"] == 1000, "sin aprobar no entra nada en el diario")
pid = d["propuestas"][0]["id"]

BASE = "http://127.0.0.1:5100"


class BotFalso:
    activo = False

    def texto(self, *_a, **_k):
        return True


app = panel.crear_panel(vigia=False, bot=BotFalso())
c = app.test_client()
csrf = app.config["CSRF_TOKEN"]
html = c.get("/", base_url=BASE).get_data(as_text=True)
ok("Radar de noticias" in html and "chapa NUEVA" in html and "chapa RECICLADA" in html and "POSIBLEMENTE RECICLADA" in html,
   "portada: bloque del radar con chapas de colores")
ok("Aprobar (simulación)" in html and "Diario de mentira" in html and "1000.00 $" in html, "portada: propuesta con botón y saldo")
ok("Escéptico:" in html and "Revisor final:" in html, "portada: los cinco papeles")
ok(c.post("/radar/aprobar", base_url=BASE, data={"id": pid}).status_code == 403, "POST /radar/aprobar sin CSRF -> 403")
r = c.post("/radar/aprobar", base_url=BASE, data={"_csrf": csrf, "id": pid, "ajax": "1"})
ok(r.status_code == 200 and r.json["ok"], "POST /radar/aprobar apunta la idea")
r = c.post("/radar/aprobar", base_url=BASE, data={"_csrf": csrf, "id": pid, "ajax": "1"})
ok(r.status_code == 409 and "aprobada" in r.json["mensaje"], "no se aprueba dos veces")
ok(c.post("/radar/aprobar", base_url=BASE, data={"_csrf": csrf, "id": "nada", "ajax": "1"}).status_code == 409, "propuesta desconocida -> 409")
d = radar.cargar_diario()
op = d["operaciones"][0]
ok(op["precio_entrada"] == 100.0 and op["fecha_entrada"] == dia(-1) and op["importe"] == 100 and op["estado"] == "abierta",
   "entra al último cierre con 100 $ de mentira")

print("Cuentas del diario")
guardar_cierres("oro", [[dia(-2), 100.0], [dia(-1), 100.0], [dia(0), 105.0]])
d = radar.actualizar_diario(hoy=HOY)
op = d["operaciones"][0]
s = radar.saldo(d)
ok(op["estado"] == "abierta" and op["resultado"] == 5.0 and s["latente"] == 5.0 and s["total"] == 1005.0 and s["cerrado"] == 1000.0,
   "abierta: +5 % sobre 100 $ = +5 $ sin cerrar")
guardar_cierres("oro", [[dia(-2), 100.0], [dia(-1), 100.0], [dia(0), 105.0], [dia(1), 90.0], [dia(2), 200.0]])
d = radar.actualizar_diario(hoy=HOY)
op = d["operaciones"][0]
s = radar.saldo(d)
ok(op["estado"] == "cerrada" and op["precio_salida"] == 90.0 and op["fecha_salida"] == dia(1) and op["resultado"] == -10.0,
   "se cierra a las 2 sesiones (90), no después")
ok(s == {**s, "cerrado": 990.0, "total": 990.0, "realizado": -10.0, "cerradas": 1, "aciertos": 0, "en_juego": 0}, "saldo 990 $")
op_baja = {"importe": 100.0, "precio_entrada": 100.0, "escenario": "baja"}
ok(radar._pnl(op_baja, 90.0) == 10.0 and radar._pnl(op_baja, 110.0) == -10.0, "idea de que baja: gana si baja")
d["propuestas"].append({"id": "vieja", "fecha": dia(-5), "mercado": "oro", "nombre": "Oro", "titulo": "t", "escenario": "sube",
                        "dias": 1, "revisor": "", "estado": "pendiente"})
d = radar.actualizar_diario(d, hoy=HOY)
ok(d["propuestas"][-1]["estado"] == "caducada", "propuesta de hace 5 días caduca")
ok(radar.aprobar("vieja")[0] is False, "una caducada no se aprueba")

print("Lección en la siguiente llamada")
respuestas.append({"noticias": [], "ideas": [], "lecciones": [{"id": op["id"], "leccion": "Un máximo no dice nada del día siguiente: bajó un 10 %."},
                                                              {"id": "otra", "leccion": "Hay que comprar más."}]})
datos2 = radar.hacer(avisar=log.append, ahora=AHORA + 86400)
ok(op["id"] in llamadas[-1]["prompt"], "la idea cerrada va en el prompt")
d = radar.cargar_diario()
ok(d["operaciones"][0]["leccion"].startswith("Un máximo no dice nada"), "lección de una línea guardada en la idea cerrada")
ok("otra" not in datos2["lecciones"], "una lección que solo es un consejo se quita")
respuestas.append({"noticias": [], "ideas": [], "lecciones": []})
radar.hacer(avisar=log.append, ahora=AHORA + 2 * 86400)
ok(op["id"] not in llamadas[-1]["prompt"], "con lección ya no se vuelve a pedir")

print("Rutas y CLI")
lanzados = []
app.lanzar_radar = lambda: lanzados.append(1) or True
r = c.post("/radar", base_url=BASE, data={"_csrf": csrf, "ajax": "1"})
ok(r.status_code == 200 and lanzados, "POST /radar lanza el radar")
ok(c.get("/radar/estado", base_url=BASE).status_code == 200, "GET /radar/estado")
import app as app_cli  # noqa: E402
respuestas.append(respuesta())
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    codigo = app_cli.cmd_radar(argparse.Namespace())
texto = salida.getvalue()
ok(codigo == 0 and "Radar de noticias" in texto and "Diario de mentira" in texto and texto.isascii(), "cmd_radar: exit 0, ASCII")
radar.preguntar_fn = None
analista.disponible_fn = lambda: False
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    codigo = app_cli.cmd_radar(argparse.Namespace())
ok(codigo == 1 and "Claude Code" in salida.getvalue(), "sin Claude: exit 1 con mensaje claro")

print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
