"""The visual office without network: sala/oficina.py with fake cards, analysis and switches, then /oficina and
/oficina/estado through the Flask test client.

    python tests/prueba_oficina.py
"""
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

TMP = Path(tempfile.mkdtemp(prefix="madriguera-oficina-"))
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

from sala import analista, bolsa, oficina  # noqa: E402
from sala import equipo as equipo_mod  # noqa: E402
import panel  # noqa: E402

fallos = []


def ok(cond, nombre):
    print(("  ok   " if cond else "  FALLO") + "  " + nombre)
    if not cond:
        fallos.append(nombre)


TARJETAS = [
    {"clave": "sp500", "nombre": "S&P 500", "tipo": "indice", "color": "verde", "titular": "Va subiendo con calma.",
     "datos": ["Precio: 5.100,00 $ (cierre del 02/10/2026)"], "numeros": {"hoy_pct": 0.4}},
    {"clave": "tesla", "nombre": "Tesla", "tipo": "accion", "color": "rojo", "titular": "Está cayendo.", "datos": ["Precio: 200,00 $"], "numeros": {"hoy_pct": -3.1}},
    {"clave": "oro", "nombre": "Oro", "tipo": "metal", "color": "amarillo", "titular": "Sin rumbo.", "datos": ["Precio: 2.400,00 $"], "numeros": {"hoy_pct": 0.1}},
    {"clave": "btc", "nombre": "Bitcoin (BTC)", "tipo": "cripto", "color": "gris", "titular": "Pocos datos.", "datos": [], "numeros": {}, "nota": "Sin histórico"},
]
ANALISIS = {"fecha": "2026-10-02", "hora": "08:05", "titular": "Mercados tranquilos", "resumen_general": "Resumen.", "aviso": "Solo describe.",
            "mercados": {"sp500": {"riesgo": {"nivel": "bajo"}, "noticia": {"texto": "La Fed mantiene tipos", "fuente": "https://ejemplo.org", "fecha": "2026-10-01"}},
                         "tesla": {"riesgo": {"nivel": "alto"}, "noticia": None}}}
AUTO = {"velas": {"nombre": "Vigilar el mercado", "activo": True}, "parte": {"nombre": "Parte diario", "activo": False}}
BANNER = {"hay": False, "titulo": "¿Hay algo que hacer hoy? No.", "texto": "Estamos en simulación."}

eq = equipo_mod.equipo()
o = oficina.estado(tarjetas=TARJETAS, analisis=ANALISIS, automatico=AUTO, banner=BANNER, equipo=eq,
                   reglas=equipo_mod.REGLAS_RIESGO, trabajos={"vigia": {"estado": "corriendo", "log": ["10:00 vigía: ok"]}}, hora="10:00:00")
deps = {d["id"]: d for d in o["departamentos"]}
nombres = ["Bolsa", "Empresas", "Materias primas", "Criptomonedas", "Análisis IA", "Radar de noticias", "Riesgo", "Sala de bots (simulación)"]
ok(o["cartel"] == "LA MADRIGUERA TRADING" and [d["nombre"] for d in o["departamentos"]] == nombres, "cartel y los ocho departamentos en orden")
ok(deps["bolsa"]["pantallas"][0]["titulo"] == "S&P 500" and deps["bolsa"]["pantallas"][0]["color"] == "verde"
   and deps["bolsa"]["pantallas"][0]["valor"] == "5.100,00 $", "Bolsa: semáforo y precio del S&P 500")
ok(deps["empresas"]["pantallas"][0]["color"] == "rojo" and deps["materias"]["pantallas"][0]["titulo"] == "Oro"
   and deps["cripto"]["pantallas"][0]["color"] == "gris", "cada mercado en su departamento")
ok("Mercados tranquilos" in deps["analisis"]["pantallas"][0]["valor"] and "1 alto" in deps["analisis"]["pantallas"][1]["valor"], "Análisis IA con el último análisis")
ok(deps["radar"]["pantallas"][0]["valor"] == "La Fed mantiene tipos" and len(deps["radar"]["pantallas"]) == 1, "Radar con la noticia guardada")
ok("1 verde" in deps["riesgo"]["pantallas"][0]["valor"] and "1 rojo" in deps["riesgo"]["pantallas"][0]["valor"], "Riesgo cuenta los semáforos")
bots = deps["bots"]["pantallas"]
ok(all("imulación" in (p["titulo"] + p["linea"]) for p in bots) and any(p["valor"] == "Encendido" for p in bots)
   and any(p["valor"] == "Corriendo" for p in bots), "Sala de bots: interruptores y trabajos, todo con «simulación»")
ok(all(len(d["trabajadores"]) >= 3 for d in o["departamentos"]), "al menos tres trabajadores por departamento")
puestos = [t["nombre"] for d in o["departamentos"] for t in d["trabajadores"]]
ok(all(m["nombre"] in puestos for m in eq), "las siete personas del equipo tienen sitio")
ok(o["luces"] == {"verde": 1, "amarillo": 1, "rojo": 1, "gris": 1}, "recuento de luces")
vacio = oficina.estado()
ok(len(vacio["departamentos"]) == 8 and all(d["pantallas"] for d in vacio["departamentos"]), "sin datos: pantallas con «sin datos»")
ok(oficina.estado(tarjetas=TARJETAS, equipo=eq) == oficina.estado(tarjetas=TARJETAS, equipo=eq), "determinista")

# ---------- routes ----------
bolsa.tarjetas = lambda cfg=None, ahora=None: TARJETAS
bolsa.banner = lambda indice=None: BANNER
analista.ultimo = lambda: ANALISIS


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
r = c.get("/oficina", base_url=BASE)
html = r.get_data(as_text=True)
ok(r.status_code == 200 and "<canvas" in html and "LA MADRIGUERA" in html and all(n in html for n in nombres), "GET /oficina con cartel, canvas y departamentos")
ok(html.count("imulación") >= 3 and all(m["nombre"] in html for m in eq) and "5.100,00 $" in html, "GET /oficina dice simulación, nombra al equipo y enseña precios")
ok("cdn" not in html.lower(), "sin CDNs")
r = c.get("/oficina/estado", base_url=BASE)
ok(r.status_code == 200 and len(r.json["departamentos"]) == 8 and r.json["simulacion"].startswith("Simulación"), "GET /oficina/estado JSON")
ok(c.get("/oficina/estado", base_url="http://evil.example:5100").status_code == 403, "Host ajeno -> 403")
ok('href="/oficina"' in c.get("/", base_url=BASE).get_data(as_text=True), "la portada enlaza la oficina")
print()
if fallos:
    print(f"{len(fallos)} fallos: " + "; ".join(fallos))
    sys.exit(1)
print("Todo bien.")
