"""La Madriguera Trading: the room, the lookout and the panel. Public data, simulation, no real money.

Usage:
    python app.py              starts the panel (and the lookout) on http://127.0.0.1:5100
    python app.py vigilar      one watcher pass (prices, alerts) and the daily report, printed
    python app.py comprobar    health check: Kraken reachable, candles today, Telegram configured
"""
import argparse
import sys
import threading
import webbrowser

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 5100   # the shorts panel uses another port, so both can run on the same PC

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def cmd_comprobar(_args):
    from panel import comprobar_todo
    inf = comprobar_todo(imprimir=True)
    print()
    if inf.errores:
        print(f"{inf.errores} cosas por arreglar.")
        return 1
    print("Todo listo.")
    return 0


def cmd_vigilar(_args):
    from sala import mercado
    try:
        alertas = mercado.vigilar()
    except OSError as err:   # the panel's lookout is writing the same files right now
        print(f"No he podido leer o guardar los datos ({err}). ¿Está el panel abierto? Pide el parte desde el panel.")
        return 1
    print(mercado.estado().get("ultimo", ""))
    for a in alertas:
        print("  " + a["texto"])
    print()
    print(mercado.parte())
    return 0


def cmd_panel(args):
    from panel import crear_panel
    url = f"http://{PANEL_HOST}:{PANEL_PORT}"
    print(f"Panel en {url}  (cierra esta ventana para apagarlo)")
    if not args.sin_navegador:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    crear_panel(vigia=True).run(host=PANEL_HOST, port=PANEL_PORT, debug=False, threaded=True)
    return 0


def main():
    parser = argparse.ArgumentParser(prog="app.py", description="La Madriguera Trading")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("panel", help="Abre el panel (por defecto)")
    p.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador")
    sub.add_parser("vigilar", help="Una pasada del vigía y el parte del día, por pantalla")
    sub.add_parser("comprobar", help="Revisa exchange, datos y Telegram")
    args = parser.parse_args()
    if not args.cmd:
        args.cmd, args.sin_navegador = "panel", False
    return {"comprobar": cmd_comprobar, "vigilar": cmd_vigilar, "panel": cmd_panel}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
