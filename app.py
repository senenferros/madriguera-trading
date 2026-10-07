"""La Madriguera Trading: the room, the lookout and the panel. Public data, simulation, no real money.

Usage:
    python app.py              starts the panel (and the lookout) on http://127.0.0.1:5100
    python app.py vigilar      one watcher pass (prices, alerts) and the daily report, printed
    python app.py comprobar    health check: Kraken reachable, candles today, Telegram configured

    python app.py historico [actualizar] [--par XBTEUR] [--dias 90] [--intervalos 1,60,1440] [--max-llamadas N]
                               Kraken history: OHLC tails, the watcher's closed days, Trades backfill (resumable, Ctrl+C safe)
    python app.py historico importar RUTA(.csv o carpeta) [--par XBTEUR] [--intervalo 1] [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD]
                               import Kraken's quarterly OHLCVT CSV files (XBTEUR_1.csv, XBTEUR_60.csv, ...)
    python app.py historico validar [--par XBTEUR] [--red]
                               check the files on disk (and, with --red, against Kraken's daily candles)
    python app.py historico reindexar
                               rebuild datos/historico/estado.json from the files
    python app.py historico derivar [--par XBTEUR] [--intervalos 60,1440]
                               rebuild the 60 and 1440-minute candles from the 1-minute ones (after a Trades backfill)
    python app.py backtest ESTRATEGIA [--par XBTEUR] [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--semillas 200]
                               walk-forward backtest, judged out of sample; exit code 0 PASA, 2 NO PASA, 3 INSUFICIENTE
    python app.py backtest --lista
                               the strategies available
    python app.py bolsa [--sin-red]
                               refresh the easy page's prices (Stooq + Kraken daily) and print the traffic lights
"""
import argparse
import sys
import threading
import webbrowser

PANEL_HOST = "127.0.0.1"
PANEL_PORT = 5100   # the shorts panel uses another port, so both can run on the same PC

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles and redirected output may be cp1252: never die on an accent, replace what cannot be shown.
    # Line-buffered so a log file (historico --dias 730 > datos\historico.log) shows progress as it happens.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


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


# ---------- historico ----------

def _fecha(texto, nombre):
    """'AAAA-MM-DD' or None; a malformed date is a ValueError with a message in Spanish."""
    from datetime import date
    if not texto:
        return None
    try:
        return date.fromisoformat(texto.strip()).isoformat()
    except ValueError:
        raise ValueError(f"{nombre} no válida: usa AAAA-MM-DD")


def cmd_historico(args):
    import requests
    from pathlib import Path
    from sala import historico, mercado
    try:
        # 'xbteur' on the terminal must be the same pair as the XBTEUR of config.yaml, on disk and in the manifest
        pares = [historico._par_valido(p) for p in args.par] if args.par else mercado.configuracion()["pares"]
        if args.accion == "importar":
            if not args.ruta:
                print("Falta la ruta: python app.py historico importar RUTA(.csv o carpeta) [--par XBTEUR] [--intervalo 1]")
                return 1
            ruta = Path(args.ruta)
            desde, hasta = _fecha(args.desde, "Fecha desde"), _fecha(args.hasta, "Fecha hasta")
            if ruta.is_dir():
                resultados = historico.importar_carpeta(ruta, pares, desde, hasta)
                if not resultados:
                    print(f"No hay ningún <PAR>_<N>.csv de {', '.join(pares)} en {ruta}")
                    return 1
            elif ruta.is_file():
                resultados = [historico.importar_csv(ruta, par=pares[0] if args.par else None, intervalo=args.intervalo, desde=desde, hasta=hasta)]
            else:
                print(f"No encuentro {ruta}")
                return 1
            for r in resultados:
                print(f"{mercado.nombre_par(r['par'])} {r['intervalo']} min: {r['filas']} filas, {r['guardadas']} velas guardadas, "
                      f"{r['descartadas']} descartadas, {r['duplicadas']} duplicadas"
                      + (f" · {historico.fecha_utc(r['desde_t'])} → {historico.fecha_utc(r['hasta_t'])}" if r["desde_t"] is not None else ""))
            _imprimir_resumen(historico, mercado)
            return 0
        if args.accion == "validar":
            for par in pares:
                r = historico.validar(par)
                for intervalo, v in r["intervalos"].items():
                    extra = (f", {len(v['ficheros_malos'])} ficheros malos ({', '.join(v['ficheros_malos'])})" if v["ficheros_malos"] else "")
                    print(f"  {mercado.nombre_par(par)} {intervalo} min: {v['velas']} velas, {len(v['huecos_largos'])} huecos > 1 h, "
                          f"{v['desordenadas']} desordenadas{extra}"
                          + (f", {v['minutos_sin_operaciones']} minutos sin operaciones" if intervalo == "1" else ""))
                    for h in v["huecos_largos"][:10]:
                        print(f"      hueco {historico._fecha_hora(h['desde'])} → {historico._fecha_hora(h['hasta'])} ({h['minutos']} min)")
                if not r["intervalos"]:
                    print(f"  {mercado.nombre_par(par)}: sin histórico (ejecuta python app.py historico)")
                    continue
                if r["concordancia_1m_60m_pct"] is not None:
                    print(f"  Concordancia 1 min → 60 min: {r['concordancia_1m_60m_pct']} %"
                          + (" · horas discordantes: " + ", ".join(historico._fecha_hora(t) for t in r["horas_discordantes"]) if r["horas_discordantes"] else ""))
                print(f"  Velas del vigía distintas del histórico: {r['discrepancias_vigia']}")
                if r["frescura_min"] is not None:
                    print(f"  Última vela de 1 min hace {r['frescura_min']} minutos")
                if args.red:
                    try:
                        historico.validar_red(par)
                    except (requests.RequestException, RuntimeError, ValueError) as err:
                        print(f"  Sin Kraken: {type(err).__name__ if isinstance(err, requests.RequestException) else err}")
            return 0
        if args.accion == "derivar":
            for par in pares:
                out = historico.derivar(par, args.intervalos.split(",") if args.intervalos else None)
                print(f"{mercado.nombre_par(par)}: " + ", ".join(f"{d} min {n} velas derivadas" for d, n in out.items()))
            _imprimir_resumen(historico, mercado)
            return 0
        if args.accion == "reindexar":
            historico.reindexar()
            _imprimir_resumen(historico, mercado)
            return 0
        # actualizar (default)
        intervalos = None
        if args.intervalos:
            try:
                intervalos = [int(x) for x in args.intervalos.split(",") if x.strip()]
            except ValueError:
                print("--intervalos: lista de minutos separados por comas, por ejemplo 1,60,1440")
                return 1
        print("Histórico de Kraken (datos públicos, sin dinero real). Ctrl+C corta y el cursor queda guardado.")
        res = historico.actualizar(pares, intervalos=intervalos, dias=args.dias, max_llamadas=args.max_llamadas)
        _imprimir_resumen(historico, mercado)
        if any(r.get("ocupado") for r in res.values()):
            return 1
        return 1 if any(r.get("error") for r in res.values()) else 0
    except KeyboardInterrupt:
        print("\nInterrumpido: el cursor queda guardado; relanza cuando quieras")
        return 1
    except historico.Ocupado as err:
        print(str(err))
        return 1
    except ValueError as err:
        print(str(err))
        return 1


def _imprimir_resumen(historico, mercado):
    res = historico.resumen()
    if not res:
        print("Sin histórico todavía.")
        return
    for par, intervalos in res.items():
        for intervalo, r in sorted(intervalos.items(), key=lambda kv: int(kv[0])):
            print(f"  {mercado.nombre_par(par)} {intervalo} min: {r['desde']} → {r['hasta']}, {historico._miles(r['velas'])} velas, "
                  f"{r['huecos_largos']} huecos > 1 h ({', '.join(r['fuentes']) or 'sin fuente'})")


# ---------- backtest ----------

def _coma(x, d=2, signo=False):
    """Number with a decimal comma for the terminal ('—' for None); `signo` adds a + to non-negative values."""
    if x is None:
        return "—"
    s = f"{x:+.{d}f}" if signo else f"{x:.{d}f}"
    return s.replace(".", ",")   # plain '-': a redirected cp1252 console cannot print U+2212


def _params(p):
    return "{" + ", ".join(f"{k}: {v}" for k, v in (p or {}).items()) + "}"


def cmd_backtest(args):
    from sala import estrategias
    if args.lista:
        for e in estrategias.lista():
            print(f"{e['id']:<14} {e['marco']:>5} min  {e['titulo']}")
        return 0
    if not args.estrategia:
        print("Falta la estrategia: python app.py backtest ESTRATEGIA [--par XBTEUR] (python app.py backtest --lista para verlas)")
        return 1
    if args.estrategia not in estrategias.REGISTRO:
        print(f"No conozco la estrategia «{args.estrategia}». Las que hay: " + ", ".join(estrategias.REGISTRO))
        return 1
    from sala import backtest, mercado
    pares = mercado.configuracion()["pares"]
    par = (args.par[0].strip().upper() if args.par and args.par[0] else "") or pares[0]
    if par not in pares:
        print(f"El par {par} no está en config.yaml (pares: {', '.join(pares)}): añádelo o elige uno de esos")
        return 1
    if args.semillas is not None and args.semillas < 1:
        print("--semillas tiene que ser al menos 1 (con menos de 20 el contraste de azar nunca baja de p = 0,05)")
        return 1
    try:
        desde, hasta = _fecha(args.desde, "Fecha desde"), _fecha(args.hasta, "Fecha hasta")
        res = backtest.correr(args.estrategia, par, desde, hasta, avisar=print, semillas=args.semillas)
    except ValueError as err:
        print(str(err))
        return 1
    print()
    _imprimir_backtest(res, backtest)
    clave = ((res.get("oos") or {}).get("veredicto") or {}).get("clave")
    return {"pasa": 0, "no_pasa": 2, "insuficiente": 3}.get(clave, 1)


def _imprimir_backtest(res, backtest):
    oos = res.get("oos") or {}
    ventanas = res.get("ventanas") or []
    print(f"Backtest {res.get('titulo')} · {res.get('nombre_par')} · {res.get('marco')} min · {res.get('desde')} → {res.get('hasta')} · "
          f"{len(ventanas)} ventanas fuera de muestra")
    for v in ventanas:
        im, om = v.get("is_m") or {}, v.get("oos_m") or {}
        isf, oosf = v.get("is_fechas") or ["?", "?"], v.get("oos_fechas") or ["?", "?"]
        print(f"  Ventana {v.get('k')}/{len(ventanas)}  IS {isf[0]}→{isf[1]} {_params(v.get('parametros'))}"
              + (" (por defecto)" if v.get("por_defecto") else "")
              + f"  {im.get('n', 0)} op  E_R {_coma(im.get('expectativa_R'), 2, True)} · OOS {oosf[0]}→{oosf[1]}  {om.get('n', 0)} op  "
              f"E_R {_coma(om.get('expectativa_R'), 2, True)}  pnl {_coma(om.get('pnl'), 0, True)} €")
    veredicto = oos.get("veredicto") or {}
    print(veredicto.get("texto", "Sin veredicto"))
    for p in (oos.get("puertas") or {}).values():
        print(f"  {'[ok]' if p.get('ok') else '[NO]'} {p.get('texto', '')}")   # ASCII marks: cp1252 consoles
    ref = res.get("referencias") or {}
    if ref:
        bh, bh30, azar = ref.get("comprar_y_mantener") or {}, ref.get("comprar_y_mantener_30") or {}, ref.get("azar") or {}
        er = azar.get("expectativa_R") or {}
        print(f"Referencias: comprar y mantener {_coma(bh.get('rentabilidad_neta_pct'), 1, True)} % (MDD {_coma(bh.get('max_drawdown_pct'), 1)} %) · "
              f"al 30 % {_coma(bh30.get('rentabilidad_neta_pct'), 1, True)} % (MDD {_coma(bh30.get('max_drawdown_pct'), 1)} %) · "
              f"azar mediana {_coma(er.get('p50'), 2, True)} R, p95 {_coma(er.get('p95'), 2, True)} R")
    m = oos.get("metricas") or {}
    is_total = res.get("is_total") or {}
    costes = res.get("costes") or {}
    print(f"Dentro de muestra prometía {_coma(is_total.get('expectativa_R'), 2, True)} R; fuera dio {_coma(m.get('expectativa_R'), 2, True)} R. "
          f"Costes: {_coma(costes.get('comisiones'), 0)} € en comisiones y {_coma(costes.get('deslizamiento'), 0)} € en deslizamiento "
          f"(taker {_coma(costes.get('comision_pct'), 2)} %, deslizamiento {_coma(costes.get('deslizamiento_pct'), 2)} %).")
    if oos.get("avisos"):
        print("Avisos: " + " · ".join(oos["avisos"]))
    if res.get("id"):
        print(f"Guardado en datos/backtests/{res['id']}.json")
    print(res.get("aviso") or backtest.AVISO_HONESTO)


def cmd_bolsa(args):
    from sala import bolsa
    if not args.sin_red:
        bolsa.actualizar(avisar=lambda m: print("  " + bolsa.ascii_(m)))
        try:
            from sala import historico
            historico.actualizar(intervalos=[1440], dias=0, max_llamadas=0, avisar=lambda m: None)
        except Exception as err:
            print(f"  Cripto sin datos nuevos ({bolsa.ascii_(str(err))})")
    print()
    b = bolsa.banner()
    print(bolsa.ascii_(b["titulo"] + " " + b["texto"]))
    print()
    for t in bolsa.tarjetas():
        print("\n".join(bolsa.texto_cli(t)))
    print()
    print("Los colores describen el mercado, no son ordenes de compra o venta. Simulacion, sin dinero real.")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="app.py", description="La Madriguera Trading")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("panel", help="Abre el panel (por defecto)")
    p.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador")
    sub.add_parser("vigilar", help="Una pasada del vigía y el parte del día, por pantalla")
    sub.add_parser("comprobar", help="Revisa exchange, datos y Telegram")
    h = sub.add_parser("historico", help="Histórico de velas de Kraken: actualizar, importar CSV, validar, reindexar, derivar")
    h.add_argument("accion", nargs="?", choices=["actualizar", "importar", "validar", "reindexar", "derivar"], default="actualizar")
    h.add_argument("ruta", nargs="?", help="Fichero .csv o carpeta con <PAR>_<N>.csv (solo importar)")
    h.add_argument("--par", action="append", help="Par de Kraken (XBTEUR); repetible; por defecto los de config.yaml")
    h.add_argument("--dias", type=int, help="Días hacia atrás que revisa el relleno por Trades (config: dias_trades)")
    h.add_argument("--intervalos", help="Colas OHLC a bajar (o, con derivar, velas a rehacer), en minutos: 1,60,1440")
    h.add_argument("--max-llamadas", type=int, dest="max_llamadas", help="Tope de llamadas a Trades en esta ejecución")
    h.add_argument("--intervalo", type=int, help="Minutos de las velas del CSV si el nombre no lo dice")
    h.add_argument("--desde", help="AAAA-MM-DD (UTC)")
    h.add_argument("--hasta", help="AAAA-MM-DD (UTC, incluido)")
    h.add_argument("--red", action="store_true", help="validar: cruza la diaria remuestreada con la de Kraken")
    b = sub.add_parser("backtest", help="Backtest walk-forward de una estrategia (simulación)")
    b.add_argument("estrategia", nargs="?", help="Id de la estrategia (ver --lista)")
    b.add_argument("--par", action="append", help="Par de Kraken (XBTEUR); por defecto el primero de config.yaml")
    b.add_argument("--desde", help="AAAA-MM-DD")
    b.add_argument("--hasta", help="AAAA-MM-DD")
    b.add_argument("--semillas", type=int, help="Entradas aleatorias de contraste (config: semillas_azar, 200)")
    b.add_argument("--lista", action="store_true", help="Lista las estrategias")
    bo = sub.add_parser("bolsa", help="Actualiza los precios de la portada y muestra los semáforos")
    bo.add_argument("--sin-red", action="store_true", dest="sin_red", help="Solo muestra lo guardado, sin descargar")
    args = parser.parse_args()
    if not args.cmd:
        args.cmd, args.sin_navegador = "panel", False
    return {"comprobar": cmd_comprobar, "vigilar": cmd_vigilar, "panel": cmd_panel,
            "historico": cmd_historico, "backtest": cmd_backtest,
            "bolsa": cmd_bolsa}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
