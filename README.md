# La Madriguera Trading

Una sala de mercados pequeña y honesta: un vigía que lee los precios públicos de Bitcoin y Ethereum en Kraken cada minuto, guarda el histórico en tu PC, avisa por Telegram cuando pasa algo, mantiene un calendario macro, lleva el diario de operaciones simuladas y, desde la Fase 1, prueba estrategias contra el histórico con walk-forward. **Simulación · datos públicos · sin dinero real.** No hay claves de exchange, no hay órdenes, no hay nada que pueda comprar o vender.

Es un proyecto independiente, extraído del departamento de mercados que vivía dentro de la oficina de Shorts. Aquí no hay vídeos ni YouTube: solo la sala.

## Qué hace hoy (Fases 0 y 1)

- **Vigía**: velas de un minuto de `XBTEUR` y `ETHEUR` (API pública de Kraken), guardadas en `datos/velas/<par>/<día>.json`. Si arranca a media mañana, rellena la parte del día que falta con velas de 5 minutos, para que la apertura y el rango sean los del día real.
- **Alertas**: rotura del máximo o del mínimo del día (una vez al día por lado, tras 30 velas) y picos de volumen (×3 sobre la mediana de la última hora, con 30 minutos de enfriamiento). Se guardan en `datos/alertas.json` y, si hay Telegram, se envían.
- **Parte diario** por Telegram a la hora de `config.yaml` (08:00 por defecto): precios, rango del día, alertas de las últimas 24 h y agenda macro de hoy y mañana. Si el envío falla, se reintenta al minuto siguiente.
- **Calendario macro**: una semilla con las reuniones de la Fed y del BCE (`datos/calendario_semilla.json`, va en el repositorio) que el documentalista amplía con Claude Code y búsqueda web cuando se lo encargas desde el panel. Si no tienes el comando `claude`, el panel lo dice y el calendario se queda con la semilla y lo que apuntes a mano en `datos/calendario.json`.
- **Diario de la sala**: notas, ideas y operaciones simuladas, con par, precio y cantidad. No se borra nada. El analista cuantitativo apunta ahí el veredicto de cada backtest.
- **Histórico de velas** (Fase 1): años de velas de Kraken en `datos/historico/`, a partir de la API pública y de los CSV trimestrales que Kraken publica. Ver «Histórico de velas».
- **Backtest** (Fase 1): seis estrategias long-only probadas con walk-forward y juzgadas solo fuera de muestra, con las cinco reglas de riesgo, comisiones y deslizamiento. Página `Backtest` del panel o `python app.py backtest`. Ver «Backtest (Fase 1)».
- **Panel web** en `http://127.0.0.1:5100`, solo accesible desde tu propio PC: la sala, la oficina (rótulo de cotizaciones, tres pantallas con los cierres de hoy, la pizarra con las reglas y las siete personas del equipo), la página de backtest y la página de comprobación.

## Las fases

| Fase | Qué | Dinero |
|------|-----|--------|
| **0** | La sala y el vigía: datos, alertas, calendario, diario. | Ninguno |
| **1** | Backtesting: el analista cuantitativo prueba las estrategias contra el histórico de Kraken guardado en `datos/historico/`, con walk-forward y las cinco reglas de riesgo. Esto es lo que hay ahora. | Ninguno |
| **2** | Paper trading: el operador en simulación ejecuta las estrategias con precios reales y dinero ficticio; el gestor de riesgo aplica las reglas. | Ninguno |
| **3** | Dinero pequeño en exchanges regulados bajo MiCA, solo con lo que la Fase 2 haya aguantado durante meses. El contable lleva el FIFO. | Pequeño |
| **4** | Canal: el cronista cuenta lo que hace la sala, con sus datos y llamando simulación a la simulación. | — |

## Las cinco reglas de riesgo

1. **1 % de riesgo por operación.**
2. **Siempre con stop.**
3. **−3 % en el día: se para.**
4. **−12 % acumulado: se apaga todo.**
5. **Máximo 30 % en un mismo activo.**

Están en la pizarra de la oficina, las aplica el motor de backtest a cada operación simulada y las aplicará el gestor de riesgo cuando haya operaciones (Fases 2 y 3).

## El equipo

Siete personas con nombre propio (se generan la primera vez y quedan en `datos/equipo.yaml`, donde puedes cambiarlos): vigía de mercado, analista cuantitativo, operador en simulación, gestor de riesgo, documentalista de datos, contable y cronista de mercados.

## Histórico de velas

El vigía guarda velas de un minuto desde que lo arrancas, pero para probar una estrategia hacen falta años. El documentalista los junta en `datos/historico/<par>/<N>m/<AAAA-MM>.json` (un fichero por mes e intervalo, mismo formato `[t, open, high, low, close, volumen]` que `datos/velas`) a partir de tres fuentes, cada una con su límite:

| Fuente | Qué da | Límite |
|--------|--------|--------|
| **OHLC** de la API pública | Las **720 velas más recientes** de cada intervalo: 12 h de 1 min, 30 días de 60 min, 2 años de diarias. | Nada más antiguo, pidas lo que pidas. Sirve para la cola, no para rellenar años. |
| **Trades** de la API pública | Operaciones una a una (1000 por llamada, ~1 llamada/s) agregadas a velas de 1 min. | BTC/EUR mueve 20–60 k operaciones al día: un día de histórico tarda entre medio minuto y un minuto; un trimestre, de 1 a 1,5 h; dos años, una noche por par. Se puede cortar con Ctrl+C y relanzar: el cursor queda guardado en `cursor_trades.json`. |
| **CSV trimestral** OHLCVT | Todo el histórico de Kraken (BTC/EUR desde 2013) en 1, 5, 15, 60, 240, 720 y 1440 min. | Lo descargas tú en el navegador (artículo de soporte de Kraken 360047124832, «Downloadable historical OHLCVT data»), lo descomprimes y lo importas: 2–5 minutos por par. |

`python app.py historico` hace, por cada par de `config.yaml`: la cola OHLC de los intervalos configurados (1, 60 y 1440 min), la consolidación de los días cerrados del vigía (`datos/velas` → histórico, solo lectura sobre `datos/velas`) y el relleno por Trades de los huecos de los últimos 90 días. En la página Backtest hay un botón que hace lo mismo con un tope de llamadas (unos minutos); lo largo se hace desde la terminal.

Detalles que conviene saber:

- Un **backtest** lee el histórico y lo remuestrea al marco de la estrategia (15 min, 60 min…) mes a mes; con un año o más de velas de 1 min importadas del CSV, el 1 min manda; sin él, las diarias de la API sirven para marcos de un día.
- `python app.py historico validar` revisa los ficheros (orden, alineación, huecos de más de una hora), cruza el 1 min remuestreado con el 60 min y el vigía con el histórico; con `--red` compara además la diaria remuestreada con la diaria de Kraken de los últimos 30 días. `python app.py comprobar` tiene una sección «Histórico».
- `historico` puede correr **con el panel abierto**, porque solo escribe en `datos/historico` (nunca en `datos/velas`). Lo que no puede es correr dos veces a la vez sobre el mismo par: hay un bloqueo `ocupado.json` con latido y el segundo proceso se niega.
- Las velas de **volumen 0** que la API OHLC inventa en los minutos sin operaciones no se guardan en el 1 min (el CSV y Trades tampoco las tienen).
- El vigía poda `datos/velas` a los `conservar_dias` de `config.yaml` (400). Como el histórico guarda copia de los días cerrados antes de que se poden, no se pierde nada; si prefieres no podar nunca, pon `conservar_dias: 0`.
- Los ficheros del histórico y los backtests no van al repositorio (`.gitignore`), ni los CSV ni los ZIP de Kraken.

## Backtest (Fase 1)

El analista cuantitativo prueba una estrategia contra el histórico de un par con **walk-forward**: elige los parámetros en un tramo de 180 días (dentro de muestra) y los juzga en los 60 días siguientes (fuera de muestra), que nunca ha visto; luego avanza 60 días y repite. Todas las ventanas fuera de muestra se cosen en una sola curva y **las puertas se evalúan solo ahí**. Las rejillas de parámetros viven en el código (4 combinaciones por estrategia); desde la página solo eliges estrategia, par y rango de fechas. El apagado del −12 % se arrastra de una ventana a la siguiente: si salta, el resto de la curva es plano.

Las seis estrategias, todas long-only y al contado: `rotura_dia` (rotura del máximo del día, 15 min, la misma idea que la alerta del vigía), `pico_volumen` (pico de volumen con vela alcista, 5 min, misma definición que la alerta), `cruce_medias` (cruce de medias con stop móvil, 240 min), `donchian` (rotura de canal, 240 min), `bandas` (reversión a la media en tendencia, 60 min) y `rsi` (RSI sobrevendido con filtro de tendencia, 15 min). `python app.py backtest --lista` las enseña.

Cada operación simulada paga **comisión del 0,40 % por lado** (taker de Kraken Pro en el tramo base; maker 0,25 %), **deslizamiento del 0,05 %** en órdenes a mercado y del 0,10 % en stops; el objetivo se llena como orden limitada, sin deslizamiento. Ida y vuelta, cerca del 0,9 %: es lo que decide lo intradía y se enseña, no se esconde. Si tu cuenta paga otra tarifa, cámbiala en `config.yaml → backtest.comision_pct`; antes de creerte un PASA repite con el doble de deslizamiento.

Las siete comprobaciones fuera de muestra (el veredicto es **PASA** solo si pasan todas):

| # | Puerta | Umbral |
|---|--------|--------|
| 1 | Expectativa por operación | > 0 R (y > 0 % del capital) |
| 2 | Profit factor | > 1,3 (sin ninguna pérdida también falla: huele a mirar al futuro) |
| 3 | Drawdown máximo | < 20 % (con el apagado al −12 % rara vez pasa del 13 %: léela junto a «apagados») |
| 4 | Operaciones | > 100 |
| 5 | Frente al azar | p < 0,05: la expectativa real contra 200 juegos de entradas aleatorias con la misma mecánica de salida (50 desde el panel) |
| 6 | Consistencia | al menos la mitad de las ventanas fuera de muestra con beneficio |
| 7 | Apagados por −12 % | 0 |

Al lado, como referencia y no como puerta: **comprar y mantener** (todo el capital) y **comprar y mantener al 30 %** (lo que permite la regla 5), con los mismos costes, y la distribución de las entradas al azar. Qué significa cada veredicto:

- **PASA las puertas de Fase 1 fuera de muestra**: la estrategia merece meses de paper trading (Fase 2). Nunca dinero real.
- **NO PASA**: la lista dice qué puerta falla y con qué número.
- **INSUFICIENTE**: no hay datos para juzgar; hacen falta al menos 4 ventanas fuera de muestra (420 días de velas), sin huecos de más de 7 días en el rango.

Además salen **avisos** que no bloquean pero que conviene leer: degradación entre dentro y fuera de muestra, profit factor sospechosamente alto, menos de un año fuera de muestra, un rango que no incluye 2022 (año bajista: una estrategia long-only parece buena en alcista), órdenes rechazadas, parámetros por defecto o inestables, y cuántos backtests llevas ya de esa estrategia y par.

Con `cruce_medias` y `donchian` (240 min) hay pocas operaciones por ventana, así que necesitan **años de datos** (4–6) para pasar de INSUFICIENTE o de «pocas operaciones»; con `rsi` y `pico_volumen` espera que los costes pesen. Y la frase que hay que repetirse: **cada backtest es una prueba más; de 20 estrategias sin ventaja real, una aprobaría las puertas por puro azar**. Por eso la página lleva la cuenta de los backtests hechos y exige, antes de creerte una estrategia, que pase en los dos pares y con otro tamaño de ventana.

Cada resultado queda en `datos/backtests/<id>.json` (parámetros por ventana, operaciones, curvas, puertas, referencias, costes) y en el diario de la sala como «idea» del analista cuantitativo. Un backtest tarda entre segundos y varios minutos; desde el panel se hace de uno en uno y comparte el procesador con el vigía: para el contraste completo (200 semillas) y marcos de 5 minutos, mejor la terminal.

## Cómo arrancarlo en Windows

Necesitas Python 3.11 (marca «Add python.exe to PATH» al instalarlo). Descomprime o clona el proyecto en `D:\LA_MADRIGUERA_TRADING`.

1. Copia `.env.example` como `.env` y rellena `TELEGRAM_TOKEN` (el de BotFather) y `TELEGRAM_CHAT` (tu chat con el bot). Si lo dejas vacío, la sala funciona igual, sin avisos.
2. Haz doble clic en **`Abrir panel.bat`**: crea el entorno `.venv` si no existe, instala lo que haga falta y abre el panel en el navegador. Cierra esa ventana para apagarlo.
3. Revisa `config.yaml` si quieres otros pares, otra hora del parte, cambiar los umbrales de las alertas o la comisión del backtest. Ojo: cada interruptor del panel reescribe `config.yaml` (los valores y los comentarios de las secciones `historico` y `backtest` se conservan; los comentarios que añadas tú a mano, no).

Desde una terminal, en la carpeta del proyecto:

```
.venv\Scripts\python.exe app.py                                   el panel (y el vigía) en http://127.0.0.1:5100
.venv\Scripts\python.exe app.py vigilar                           una pasada del vigía y el parte del día, por pantalla
.venv\Scripts\python.exe app.py comprobar                         Kraken, velas de hoy, histórico, backtests, Telegram y Claude Code
.venv\Scripts\python.exe app.py historico [actualizar]            cola OHLC + días del vigía + relleno por Trades
      [--par XBTEUR] [--dias 90] [--intervalos 1,60,1440] [--max-llamadas N]
.venv\Scripts\python.exe app.py historico importar RUTA           un .csv o la carpeta con los <PAR>_<N>.csv de Kraken
      [--par XBTEUR] [--intervalo 1] [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD]
.venv\Scripts\python.exe app.py historico validar [--par XBTEUR] [--red]
.venv\Scripts\python.exe app.py historico reindexar               reconstruye el manifiesto estado.json
.venv\Scripts\python.exe app.py backtest ESTRATEGIA               [--par XBTEUR] [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--semillas 200]
.venv\Scripts\python.exe app.py backtest --lista                  las seis estrategias
```

`app.py backtest` devuelve 0 si PASA, 2 si NO PASA, 3 si INSUFICIENTE y 1 si hay un error (por ejemplo, sin histórico). Mientras el panel está abierto, el vigía es el único que escribe en `datos/velas`; `app.py vigilar` es para cuando el panel está cerrado. `app.py historico` y `app.py backtest` sí pueden correr con el panel abierto.

### Qué ejecutar en tu PC la primera vez

Kraken solo es alcanzable desde tu PC: nada del histórico se ha podido probar contra la API real, y la primera ejecución puede descubrir detalles (claves de la respuesta, límites de tasa) que las pruebas sin red no reproducen. Primera tarea: un solo par y leer el registro.

1. `python tests\prueba_backtest.py` y `python tests\prueba.py` (sin red; las dos deben decir «Todo bien.»).
2. `python app.py historico --par XBTEUR` (unos 5 minutos de cola OHLC y consolidación; el relleno por Trades de los últimos 90 días tarda 1–1,5 h la primera vez y un minuto al día después). Se puede cortar con Ctrl+C y relanzar.
3. Recomendado: descarga en el navegador el CSV trimestral OHLCVT de Kraken (artículo 360047124832), descomprímelo y `python app.py historico importar D:\descargas\Kraken_OHLCVT --par XBTEUR --par ETHEUR --desde 2019-01-01` (2–5 minutos por par). Después, `python app.py historico` solo rellena el hueco entre el fin del CSV y hoy.
4. Alternativa sin CSV: `python app.py historico --dias 730` (una o dos noches por par, reanudable).
5. `python app.py historico validar --red` (cruza la diaria remuestreada con la diaria de Kraken de los últimos 30 días) y `python app.py comprobar` (sección «Histórico»).
6. `python app.py backtest rotura_dia --par XBTEUR`, o el botón de la página Backtest. Con `rsi` y `pico_volumen` espera que los costes pesen; con `cruce_medias` y `donchian` espera INSUFICIENTE o NO PASA por número de operaciones hasta tener 4–6 años de datos.
7. Ajusta `config.yaml → backtest.comision_pct` a la tarifa real de tu cuenta (kraken.com/features/fee-schedule) y repite con el doble de deslizamiento antes de creerte un PASA.

Con el panel abierto, `app.py historico` puede correr (solo escribe en `datos/historico`), pero no dos a la vez sobre el mismo par (bloqueo `ocupado.json`).

## Estructura

```
app.py                     CLI: panel, vigilar, comprobar, historico, backtest
nucleo.py                  carpetas, config.yaml, .env, informe de comprobación
panel.py                   Flask: páginas, JSON de estado, diario, calendario, interruptores, bucle del vigía, trabajos de backtest e histórico
sala/mercado.py            velas, alertas, parte, calendario, diario
sala/historico.py          histórico de Kraken: OHLC + Trades + CSV, lector de rangos, remuestreo, validación
sala/estrategias.py        indicadores puros, contrato Estrategia, las seis estrategias
sala/backtest.py           motor con las cinco reglas, métricas, walk-forward, referencias, azar, puertas, veredicto
sala/telegram.py           envío de mensajes (sin botones ni escucha)
sala/claude.py             Claude Code en modo headless, opcional (solo para el calendario)
sala/equipo.py             las siete personas y las reglas de riesgo
templates/                 base, sala, oficina, backtest, comprobar
tests/prueba.py            prueba sin red de la Fase 0: python tests\prueba.py
tests/prueba_backtest.py   prueba sin red de la Fase 1 (histórico, motor, estrategias, veredicto, página, CLI): python tests\prueba_backtest.py
datos/                     lo que genera la sala (no va al repositorio, salvo la semilla del calendario)
datos/historico/           <par>/<N>m/<AAAA-MM>.json, estado.json, cursor_trades.json, ocupado.json
datos/backtests/           <id>.json e indice.json
```

## Aviso

Esto no es asesoramiento financiero ni una recomendación de compra o venta. La mayoría de quien hace trading a corto plazo pierde dinero. Todo lo que hace esta app es simulación con datos públicos. Cada backtest es una prueba más: de 20 estrategias sin ventaja real, una aprobaría las puertas por puro azar.
