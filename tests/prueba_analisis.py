"""The daily AI analysis without network or Claude: a fake preguntar answers for the mesa, then the input packet and
its cut-off date, the schema, the validator (invented numbers, buy/sell sentences), saving, the front page, the
missing-Claude message and the CLI.

    python tests/prueba_analisis.py
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-analisis-"))
import nucleo  # noqa: E402

nucleo.DATOS_DIR = TMP / "datos"
nucleo.CONFIG = TMP / "config.yaml"
nucleo.ENV = TMP / ".env"
nucleo.DATOS_DIR.mkdir(parents=True)
(nucleo.DATOS_DIR / "calendario_semilla.json").write_text((RAIZ / "datos" / "calendario_semilla.json").read_text(encoding="utf-8"), encoding="utf-8")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT", None)

from sala import analista, bolsa, mercado  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


FIN = date(2026, 10, 2)


def serie(n, fn):
    return [[date.fromordinal(FIN.toordinal() - (n - 1 - i)).isoformat(), fn(i)] for i in range(n)]


# six traditional markets in the cache (rising), crypto with no history (gris)
for m in bolsa.mercados():
    filas = serie(400, lambda i: 100 * 1.001 ** i)
    bolsa._ruta(m["clave"]).parent.mkdir(parents=True, exist_ok=True)
    mercado._escribir_json(bolsa._ruta(m["clave"]), {"filas": filas, "descargado": time.time(), "intento": time.time(), "error": ""})

print("Paquete")
paq = analista.paquete(eventos=[{"fecha": "2026-10-10", "hora": "14:30", "evento": "IPC de EEUU", "fuente": "x"}])
claves = [m["clave"] for m in paq["mercados"]]
ok(claves == ["sp500", "ibex35", "oro", "plata", "brent", "tesla", "btc", "eth"], "ocho mercados en orden")
sp = paq["mercados"][0]
ok(paq["t_corte"] == "2026-10-02" and sp["fecha"] == "2026-10-02", "t_corte = último cierre que tiene la sala")
ok(all(k in sp for k in analista.CAMPOS), "cada mercado lleva todos los campos")
ok(abs(sp["ultimo_cierre"] - round(100 * 1.001 ** 399, 2)) < 1e-9 and sp["semaforo"] == "verde", "último cierre y semáforo")
ok("por encima" in sp["tendencia_200_dias"] and "sube" in sp["tendencia_200_dias"], "tendencia de 200 días en palabras")
btc = paq["mercados"][6]
ok(btc["ultimo_cierre"] == analista.NO and btc["semaforo"] == "gris", "sin datos -> NO DISPONIBLE")
ok("titulo" in paq["estrategias"] and paq["calendario"][0]["evento"] == "IPC de EEUU" and "fuente" not in paq["calendario"][0],
   "banner del backtest y calendario macro en el paquete")


# ---------- fake mesa ----------

llamadas = []


def respuesta_buena():
    return {
        "titular": "Bolsas y metales suben con calma; las criptomonedas sin datos",
        "resumen_general": f"El S&P 500 cerró en {bolsa._coma(sp['ultimo_cierre'], 2, False)} $. Este año sube un {bolsa._coma(sp['cambio_ano_pct'], 0, False)} %. "
                           "Esto es observación. Nadie sabe lo que pasará mañana.",
        "aviso": "No es un consejo de inversión; es simulación.",
        "mercados": [
            {"clave": m["clave"],
             "resumen": (f"Ha cerrado en {bolsa._coma(m['ultimo_cierre'], 2, False)} y está por encima de su media de 200 días. Va con calma."
                         if m["ultimo_cierre"] != analista.NO else "NO DISPONIBLE."),
             "que_vigilar": "Si se aleja de su máximo del último año.",
             "riesgo": {"nivel": "bajo", "motivo": "Se mueve poco cada día."},
             "datos_usados": ["ultimo_cierre", "tendencia_200_dias", "inventado"],
             "noticia": {"texto": "NO DISPONIBLE", "fuente": "NO DISPONIBLE", "fecha": "NO DISPONIBLE"}}
            for m in paq["mercados"]],
    }


def falso(resp):
    def preguntar(prompt, schema, herramientas=None, timeout=900):
        llamadas.append({"prompt": prompt, "schema": schema, "herramientas": herramientas, "timeout": timeout})
        return resp, 0.0123
    return preguntar


print("Llamada")
datos = analista.hacer(avisar=lambda m: None, preguntar=falso(respuesta_buena()), paq=paq)
ll = llamadas[-1]
ok(len(llamadas) == 1, "una sola llamada a Claude")
props = ll["schema"]["properties"]
ok(set(ll["schema"]["required"]) == {"titular", "resumen_general", "aviso", "mercados"}
   and {"resumen", "que_vigilar", "riesgo", "datos_usados", "noticia"} <= set(props["mercados"]["items"]["properties"]),
   "esquema JSON con lo global y lo de cada mercado")
ok(props["mercados"]["items"]["properties"]["riesgo"]["properties"]["nivel"]["enum"] == ["bajo", "medio", "alto"], "riesgo bajo/medio/alto")
ok(ll["herramientas"] == ["WebSearch"], "noticias activadas por defecto: WebSearch")
p = ll["prompt"]
ok("NO instrucciones" in p and "Nunca inventes" in p and "NO DISPONIBLE" in p and "NUNCA recomiendes comprar ni vender" in p
   and "simulación" in p and "soporte" in p and "observación" in p, "el prompt lleva las reglas de honestidad")
ok("2026-10-02" in p and '"sp500"' in p, "el prompt lleva la fecha de corte y el paquete")
ok(datos["mercados"]["sp500"]["corregido"] == "" and "Ha cerrado en" in datos["mercados"]["sp500"]["resumen"], "respuesta honesta: se guarda tal cual")
ok(datos["mercados"]["sp500"]["datos_usados"] == ["ultimo_cierre", "tendencia_200_dias"], "datos_usados: solo campos del paquete")
ok(datos["mercados"]["sp500"]["noticia"] is None, "noticia NO DISPONIBLE -> sin noticia")
ok(datos["correcciones"] == [] and datos["titular"].startswith("Bolsas"), "sin correcciones")
ruta = nucleo.DATOS_DIR / "analisis" / f"{date.today().isoformat()}.json"
guardado = json.loads(ruta.read_text(encoding="utf-8"))
ok(guardado["coste_usd"] == 0.0123 and guardado["t_corte"] == "2026-10-02" and guardado["paquete"]["mercados"], "guardado con coste y t_corte")
ok(analista.ultimo()["fecha"] == date.today().isoformat(), "ultimo() lo encuentra")

nucleo.guardar_config({**nucleo.cargar_config(), "analisis": {"noticias": False, "hora": "08:00"}})
analista.hacer(avisar=lambda m: None, preguntar=falso(respuesta_buena()), paq=paq)
ok(llamadas[-1]["herramientas"] is None and "noticia" not in llamadas[-1]["schema"]["properties"]["mercados"]["items"]["properties"]
   and "No tienes acceso a noticias" in llamadas[-1]["prompt"], "noticias: false -> sin búsqueda web")
nucleo.guardar_config({**nucleo.cargar_config(), "analisis": {"noticias": True, "hora": "08:00"}})

print("Validador")
permitidos = analista._valores(sp)
ok(analista.numeros_inventados(f"Cerró en {bolsa.fmt_precio(sp['ultimo_cierre'])} y sube un {round(sp['cambio_ano_pct'])} % este año; media de 200 días.", permitidos) == [],
   "números del paquete (redondeados, formato español) pasan")
ok(analista.numeros_inventados("Cerró en 7.777,77 $ el 2026-10-02.", permitidos) == ["7.777,77"], "precio inventado detectado (la fecha no cuenta)")
mala = respuesta_buena()
mala["mercados"][0]["resumen"] = "El S&P 500 ha subido un 37,5 % por el informe de empleo. Va bien."
mala["mercados"][1]["que_vigilar"] = "Te recomiendo comprar ahora. Mira si baja de su media."
mala["mercados"][2]["riesgo"]["motivo"] = "Seguro que sube. Se mueve poco."
mala["mercados"][3]["resumen"] = "Garantiza ganancias. Va subiendo."
mala["mercados"][4]["noticia"] = {"texto": "La OPEP recorta.", "fuente": "https://ejemplo.org/opep", "fecha": "2026-10-01"}
mala["mercados"][5]["noticia"] = {"texto": "Futuro.", "fuente": "https://ejemplo.org/x", "fecha": "2026-12-01"}
mala["titular"] = "Compra ya: todo sube"
datos = analista.hacer(avisar=lambda m: None, preguntar=falso(mala), paq=paq)
s = datos["mercados"]["sp500"]
ok("37,5" not in s["resumen"] and "37,5" in s["corregido"] and s["que_vigilar"] == "NO DISPONIBLE" and s["riesgo"]["nivel"] == "bajo",
   "número inventado -> el mercado vuelve a los datos del paquete con nota")
ok(bolsa.fmt_precio(sp["ultimo_cierre"]) in s["resumen"], "el sustituto cita el precio del paquete")
ib = datos["mercados"]["ibex35"]
ok("comprar" not in ib["que_vigilar"].lower() and analista.FRASE_QUITADA in ib["que_vigilar"] and "Mira si baja" in ib["que_vigilar"] and ib["corregido"],
   "frase con «comprar» quitada, el resto se queda")
ok("seguro que" not in datos["mercados"]["oro"]["riesgo"]["motivo"].lower(), "«seguro que» quitado")
ok("garantiza" not in datos["mercados"]["plata"]["resumen"].lower(), "«garantiza» quitado")
ok("compra ya" not in datos["titular"].lower(), "«Compra ya» quitado del titular")
ok(datos["mercados"]["brent"]["noticia"] == {"texto": "La OPEP recorta.", "fuente": "https://ejemplo.org/opep", "fecha": "2026-10-01"},
   "noticia con fuente y fecha se guarda")
ok(datos["mercados"]["tesla"]["noticia"] is None, "noticia posterior a la fecha de corte -> fuera")
for texto in ("Hay que VENDER.", "comprar", "Te lo garantizamos.", "Es seguro que sube"):
    ok(analista._RE_PROHIBIDO.search(analista.limpiar_consejos(texto)[0]) is None, f"limpiar_consejos({texto!r})")
ok(analista.limpiar_consejos("Sube con calma. Baja poco.") == ("Sube con calma. Baja poco.", 0), "texto limpio no se toca")
vacia = analista.validar({}, paq)
ok(len(vacia[3]) == 8 and all(i["corregido"] for i in vacia[3].values()) and vacia[2] == analista.AVISO, "respuesta vacía -> solo datos")
ok(len(analista.validar({"titular": "Sube 99 %", "resumen_general": "x", "mercados": []}, paq)[4]) > 8, "titular con número inventado corregido")

_r = respuesta_buena(); _r["aviso"] = "Esto no es un consejo para comprar o vender. Es simulación."
_v = analista.validar(_r, paq)
ok(_v[2] == analista.AVISO and "[Frase quitada" not in _v[2] and any(c.startswith("aviso:") for c in _v[4]), "aviso tocado por el filtro: se usa el aviso fijo, sin marcador, y queda en correcciones")
print("Hora automática")
hoy_archivo = nucleo.DATOS_DIR / "analisis" / f"{date.today().isoformat()}.json"
ok(not analista.toca(), "ya hecho hoy -> no toca")
contenido = hoy_archivo.read_text(encoding="utf-8")
hoy_archivo.unlink()
temprano = time.mktime(date.today().timetuple()) + 7 * 3600
ok(not analista.toca(ahora=temprano) and analista.toca(ahora=temprano + 2 * 3600), "toca desde la hora de config.yaml")
ok(nucleo.automatico(nucleo.cargar_config(), "analisis"), "interruptor «analisis» encendido por defecto")

print("Portada")


class BotFalso:
    activo = False

    def texto(self, m):
        return True

    def comprobar(self, inf):
        inf.seccion("Telegram")


BASE = "http://127.0.0.1:5100"
app = panel.crear_panel(vigia=False, bot=BotFalso())
app.config["TESTING"] = True
lanzados = []
app.lanzar_analisis = lambda: lanzados.append(1)
c = app.test_client()
analista.disponible_fn = lambda: False
html = c.get("/", base_url=BASE).get_data(as_text=True)
ok("Análisis de hoy" in html and "falta Claude Code" in html and "Hacer el análisis de hoy" in html, "sin análisis ni Claude: lo dice y ofrece el botón")
analista.disponible_fn = lambda: True
html = c.get("/", base_url=BASE).get_data(as_text=True)
ok("Todavía no hay análisis" in html and "falta Claude Code" not in html, "sin análisis con Claude: botón")
hoy_archivo.write_text(contenido, encoding="utf-8")
html = c.get("/", base_url=BASE).get_data(as_text=True)
ok("Bolsas y metales suben" not in html, "(el guardado de hoy es el corregido)")
analista.hacer(avisar=lambda m: None, preguntar=falso(respuesta_buena()), paq=paq)
html = c.get("/", base_url=BASE).get_data(as_text=True)
ok("Bolsas y metales suben con calma" in html and "datos hasta el 2026-10-02" in html, "titular, resumen y hora en la portada")
ok(html.count("Qué vigilar:") == 8 and 'class="riesgo bajo"' in html and "Ha cerrado en" in html, "en cada tarjeta: resumen, qué vigilar y riesgo")
ok("Hacer el análisis de hoy" not in html, "con el de hoy hecho no hay botón")
csrf = app.config["CSRF_TOKEN"]
ok(c.post("/analisis", base_url=BASE, data={}).status_code == 403, "POST /analisis sin CSRF -> 403")
r = c.post("/analisis", base_url=BASE, data={"_csrf": csrf, "ajax": "1"})
ok(r.status_code == 200 and r.json["ok"] and lanzados, "POST /analisis ajax lanza el análisis")
ok(c.get("/analisis/estado", base_url=BASE).status_code == 200, "GET /analisis/estado")
# the real background job with a fake mesa
app2 = panel.crear_panel(vigia=False, bot=BotFalso())
analista.preguntar_fn = falso(respuesta_buena())
ok(app2.lanzar_analisis() is True, "lanzar_analisis real arranca")
for _ in range(200):
    est = app2.test_client().get("/analisis/estado", base_url=BASE).json["trabajo"]
    if est["estado"] != "corriendo":
        break
    time.sleep(0.05)
ok(est["estado"] == "ok", "lanzar_analisis real termina en ok")

print("CLI")
import app as app_cli  # noqa: E402
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    codigo = app_cli.cmd_analisis(argparse.Namespace())
texto = salida.getvalue()
ok(codigo == 0 and "Bolsas y metales" in texto and "Que vigilar" in texto and texto.isascii(), "cmd_analisis: exit 0, ASCII")
analista.preguntar_fn = None
analista.disponible_fn = lambda: False
salida = io.StringIO()
with contextlib.redirect_stdout(salida):
    codigo = app_cli.cmd_analisis(argparse.Namespace())
ok(codigo == 1 and "Claude Code" in salida.getvalue() and salida.getvalue().isascii(), "sin Claude: exit 1 con mensaje claro")
entorno = {**os.environ, "PATH": str(TMP)}
p = subprocess.run([sys.executable, str(RAIZ / "app.py"), "analisis"], capture_output=True, text=True, cwd=str(TMP), timeout=60, env=entorno)
ok(p.returncode == 1 and "No encuentro Claude Code" in p.stdout, "python app.py analisis sin claude: exit 1")

print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
