"""The web panel: the room's page, a simple office view, a JSON state the pages poll, the journal and the switches.
Loopback only: it answers requests addressed to this machine, and form posts carry a CSRF token.

The background lookout (one watcher pass per minute, Telegram alerts, the daily report) lives here too, so the panel
is the only process touching datos/ while it runs.
"""
import hmac
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import Flask, abort, flash, redirect, render_template, request, url_for

import nucleo
from sala import equipo as equipo_mod
from sala import mercado, telegram

HOSTS_PERMITIDOS = {"127.0.0.1", "localhost", "::1"}


def crear_panel(vigia=True, bot=None):
    """vigia=False (tests, CLI) skips the background thread. `bot` can be a fake; None means the real one from .env."""
    app = Flask(__name__)
    ruta_clave = nucleo.DATOS_DIR / "clave_sesion.bin"
    if not ruta_clave.is_file() or len(ruta_clave.read_bytes()) < 32:
        ruta_clave.parent.mkdir(parents=True, exist_ok=True)
        ruta_clave.write_bytes(secrets.token_bytes(32))
    app.secret_key = ruta_clave.read_bytes()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", MAX_CONTENT_LENGTH=1024 * 1024)
    # embedded in every form; kept across restarts so a page left open keeps working after a restart
    ruta_csrf = nucleo.DATOS_DIR / "csrf_token.txt"
    csrf_token = ruta_csrf.read_text(encoding="utf-8").strip() if ruta_csrf.is_file() else ""
    if len(csrf_token) < 32:
        csrf_token = secrets.token_urlsafe(32)
        ruta_csrf.parent.mkdir(parents=True, exist_ok=True)
        ruta_csrf.write_text(csrf_token, encoding="utf-8")
    app.config["CSRF_TOKEN"] = csrf_token

    bot = bot if bot is not None else telegram.Bot()
    trabajos = {}   # "vigia" -> {estado, log}, "calendario" -> {estado, log}

    # ---------- security ----------

    @app.before_request
    def solo_local():
        # Only accept requests addressed to this machine, and form posts coming from the panel itself
        host = request.host.rsplit(":", 1)[0] if not request.host.startswith("[") else request.host.split("]")[0].lstrip("[")
        if host not in HOSTS_PERMITIDOS:
            abort(403)
        if request.method == "POST":
            origen = request.headers.get("Origin") or request.headers.get("Referer")
            if origen and urlparse(origen).hostname not in HOSTS_PERMITIDOS:
                abort(403)
            if not hmac.compare_digest(request.form.get("_csrf", ""), csrf_token):
                return ('<meta charset="utf-8"><body style="font-family:sans-serif;background:#111;color:#eee;padding:30px">'
                        '<h2>Esta página estaba abierta desde antes de reiniciar el panel</h2>'
                        '<p>Vuelve, recarga la página (F5) y repite lo que estabas haciendo.</p>'
                        '<p><a style="color:#4cc38a" href="/">← Volver</a></p>', 403)

    @app.after_request
    def cabeceras(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.context_processor
    def globales():
        return {"csrf_token": csrf_token, "nombre_sala": nucleo.cargar_config().get("nombre", "La Madriguera Trading")}

    def _automaticos():
        cfg = nucleo.cargar_config()
        return {k: {"nombre": v, "activo": nucleo.automatico(cfg, k)} for k, v in equipo_mod.AUTOMATICOS.items()}

    # ---------- the lookout loop ----------

    def vigilar_bucle():
        # One pass a minute while 'velas' is on; alerts and the daily report by Telegram when their switch is on.
        # The day is marked as reported only when Telegram accepted the message, so a failed send is retried a minute later.
        while True:
            try:
                cfg = nucleo.cargar_config()
                if nucleo.automatico(cfg, "velas"):
                    trabajo = {"estado": "leyendo", "log": []}
                    trabajos["vigia"] = trabajo
                    alertas = mercado.vigilar(lambda m: trabajo["log"].append(f"{time.strftime('%H:%M:%S')}  {m}"), cfg=cfg)
                    trabajo["estado"] = "ok"
                    if alertas and nucleo.automatico(cfg, "alertas"):
                        bot.texto("\n".join(a["texto"] for a in alertas) + "\n(La Madriguera Trading · datos públicos, sin dinero real)")
                if nucleo.automatico(cfg, "parte") and mercado.toca_parte(cfg=cfg):
                    if bot.texto(mercado.parte(cfg)):
                        mercado.anotar_parte_enviado()
            except Exception as e:   # never let the lookout die
                trabajos["vigia"] = {"estado": "error", "log": [f"{time.strftime('%H:%M:%S')}  Error: {e}"]}
            time.sleep(60)

    if vigia:
        threading.Thread(target=vigilar_bucle, daemon=True).start()

    def lanzar_calendario():
        """The archivist looks up confirmed macro dates (Claude + web search), on the owner's order."""
        if trabajos.get("calendario", {}).get("estado") == "corriendo":
            return
        trabajo = {"estado": "corriendo", "log": []}
        trabajos["calendario"] = trabajo

        def correr():
            try:
                mercado.actualizar_calendario(lambda m: trabajo["log"].append(f"{time.strftime('%H:%M:%S')}  {m}"))
                trabajo["estado"] = "ok"
            except Exception as e:
                trabajo["log"].append(f"{time.strftime('%H:%M:%S')}  Error: {e}")
                trabajo["estado"] = "error"

        threading.Thread(target=correr, daemon=True).start()

    app.lanzar_calendario = lanzar_calendario   # the tests swap it for a fake

    # ---------- pages ----------

    @app.route("/")
    def sala():
        return render_template("sala.html", r=mercado.resumen(), automatico=_automaticos(), trabajo=trabajos.get("vigia"),
                               calendario_job=trabajos.get("calendario"), telegram=bot.activo, reglas=equipo_mod.REGLAS_RIESGO)

    @app.route("/oficina")
    def oficina():
        return render_template("oficina.html", r=mercado.resumen_corto(), equipo=equipo_mod.equipo(),
                               reglas=equipo_mod.REGLAS_RIESGO, automatico=_automaticos())

    @app.get("/estado")
    def estado():
        """Live numbers for the pages (polled every minute)."""
        r = mercado.resumen()
        r["calendario_job"] = trabajos.get("calendario")
        r["automatico"] = _automaticos()
        return r

    @app.post("/diario")
    def anotar_diario():
        f = request.form
        try:
            mercado.anotar_diario(f.get("tipo", "nota"), f.get("texto", ""), f.get("par", ""),
                                  mercado.numero(f.get("precio"), "Precio"), mercado.numero(f.get("cantidad"), "Cantidad"))
        except ValueError as err:
            if f.get("ajax"):
                return {"ok": False, "error": str(err)}, 400
            flash(str(err), "error")
            return redirect(url_for("sala") + "#diario")
        if f.get("ajax"):
            return {"ok": True, "diario": mercado.diario(100)}
        flash("Apuntado en el diario.", "ok")
        return redirect(url_for("sala") + "#diario")

    @app.post("/calendario")
    def encargar_calendario():
        app.lanzar_calendario()
        if request.form.get("ajax"):
            return {"ok": True, "trabajo": trabajos.get("calendario")}
        flash("El documentalista está buscando fechas confirmadas; tarda unos minutos.", "ok")
        return redirect(url_for("sala") + "#calendario")

    @app.post("/automatico")
    def cambiar_automatico():
        tarea = request.form.get("tarea", "")
        if tarea not in equipo_mod.AUTOMATICOS:
            abort(404)
        cfg = nucleo.cambiar_automatico(tarea, request.form.get("activo") == "1")
        return {"ok": True, "activo": nucleo.automatico(cfg, tarea)}

    @app.post("/parte")
    def parte():
        # Built from what the lookout already has on disk (at most a minute old): a second watcher pass from a request
        # would race with the lookout and its alerts would never reach Telegram
        texto = mercado.parte()
        enviado = None
        if request.form.get("enviar") == "1":
            enviado = bot.texto(texto)
        return {"ok": True, "texto": texto, "enviado": enviado}

    @app.route("/comprobar")
    def comprobar():
        return render_template("comprobar.html", inf=comprobar_todo(bot))

    return app


def comprobar_todo(bot=None, imprimir=False):
    import requests
    inf = nucleo.Informe(imprimir)
    bot = bot if bot is not None else telegram.Bot()
    try:
        mercado.comprobar(inf)
    except requests.RequestException as e:
        inf.error("Exchange", f"(sin conexión: {type(e).__name__})")
    bot.comprobar(inf)
    from sala import claude
    inf.seccion("Claude Code (opcional, solo para el calendario)")
    inf.ok("claude") if claude.disponible() else inf.aviso("claude", "(no está en el PATH: el calendario se queda con la semilla y lo que apuntes a mano)")
    inf.fecha = time.strftime("%d/%m/%Y %H:%M")
    return inf
