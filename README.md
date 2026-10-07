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
- **Backtest** (Fase 1): seis estrategias long-only de corto plazo y tres diarias de medio plazo (también en índices, empresas, materias primas y cripto de Yahoo: S&P 500, IBEX, Nasdaq 100, Euro Stoxx 50, DAX, oro, plata, Brent, Tesla, Nvidia, Apple, Microsoft, Solana, XRP, BNB, Dogecoin y Cardano) probadas con walk-forward y juzgadas solo fuera de muestra, con las cinco reglas de riesgo, comisiones y deslizamiento. Página `Backtest` del panel o `python app.py backtest`. Ver «Backtest (Fase 1)».
- **Panel web** en `http://127.0.0.1:5100`, solo accesible desde tu propio PC: la sala, la oficina (`/oficina`: vista isométrica al estilo de la oficina de Shorts, con el cartel LA MADRIGUERA TRADING, ocho departamentos con su nombre pintado en el suelo —Bolsa, Empresas, Materias primas, Criptomonedas, Análisis IA, Radar de noticias, Riesgo y Sala de bots (simulación)—, pantallas con los semáforos y precios de la caché de bolsa, el último análisis del día y el estado de los bots, y trabajadores animados en sus mesas; los datos salen de `sala/oficina.py` y de `/oficina/estado`), la página de backtest y la página de comprobación.

## Portada fácil

Al abrir el panel (`http://127.0.0.1:5100/`) lo primero que ves es la **portada fácil**, pensada para quien no sabe nada de bolsa. Arriba, una pregunta con respuesta honesta: **«¿Hay algo que hacer hoy?»**. Mientras ninguna estrategia apruebe el examen del backtest, la respuesta es «No». Si alguna aprueba, lo dice y aclara que lo único que haría es empezar a practicar con dinero ficticio.

Debajo, una tarjeta por mercado con un **semáforo**, en cuatro grupos: **Bolsa** (S&P 500, IBEX 35, Nasdaq 100, Euro Stoxx 50, DAX), **Empresas** (Tesla, Nvidia, Apple, Microsoft), **Materias primas** (oro, plata, petróleo Brent) y **Criptomonedas** (Solana, XRP, BNB, Dogecoin y Cardano desde Yahoo; Bitcoin y Ethereum desde Kraken). Son 19 tarjetas; el grupo sale del campo `tipo` de `config.yaml → mercados_extra`. El color dice **cómo está el mercado, no lo que tienes que hacer**:

- **Verde**: sube con calma (por encima de su media de 200 días, que va hacia arriba; a menos de un 10 % de su máximo del año; menos de un 2,5 % de movimiento diario de media).
- **Amarillo**: se mueve mucho o va sin rumbo.
- **Rojo**: cae fuerte (un 20 % o más por debajo de su máximo del año, o por debajo de su media de 200 días, que baja, y en negativo este mes).
- **Gris**: aún no hay datos suficientes.

Cada tarjeta enseña el precio, cuánto ha cambiado hoy, este mes y este año, y un «¿Qué significa?» que explica en una línea qué es ese mercado. Los precios de bolsa son diarios y gratuitos, de Yahoo Finance (se guardan en `datos/bolsa/` y se descargan como mucho cada 6 horas; si falla la conexión, la tarjeta dice «sin datos nuevos desde …»). Bitcoin y Ethereum salen del histórico de Kraken. El botón **«Actualizar precios»** los refresca. Los mercados se cambian en `config.yaml`, sección `mercados_extra`. Todo lo demás (la sala de siempre) está en **«Modo experto»** (`/sala`).

## Análisis de cada día

Cada día a las 08:00 (se cambia en `config.yaml`, sección `analisis`) una IA escribe un resumen de los 8 mercados de
la portada: un titular, unas frases para todos, y en cada tarjeta qué está pasando, qué vigilar y si el riesgo es bajo,
medio o alto. También con el botón «Hacer el análisis de hoy» o con `python app.py analisis`. Necesita Claude Code
(el programa `claude`) instalado y con la sesión iniciada; si falta, la portada lo dice.

Es honesto a propósito: la IA solo ve los números que ya tiene la sala (con su fecha de corte), y si cita un número
que no está ahí, ese mercado se muestra solo con los datos. Cualquier frase que suene a «compra» o «vende» se borra.
Con `noticias: true` puede añadir una noticia por mercado, siempre con su fecha y su fuente. Se guarda en
`datos/analisis/`, con lo que ha costado. Se puede apagar en el modo experto (interruptor «Análisis de cada día»).
No es un consejo de inversión.

## Radar de noticias y diario de mentira

Basado en la guía «AI-Trading Web-Crawler» de @seb.ai, recortada para quien no sabe nada de trading. Cada día a las
08:30 (`config.yaml`, sección `radar: {activo, hora}`; interruptor «Radar de noticias» en el modo experto) una sola
llamada a Claude Code con búsqueda web reúne noticias de los mercados de la sala (`mercados_extra` más BTC y ETH): título,
fuente, fecha, mercados afectados y un resumen de una frase. También con «Pasar el radar de hoy» o `python app.py radar`.

- **Detector de noticias viejas** (sin IA): cada noticia se compara con las de los últimos 14 días de `datos/radar/`
  (título normalizado, parecido y URL) y con su fecha. Chapas: **NUEVA**, **ACTUALIZACIÓN** (misma historia con algo
  nuevo), **DUPLICADA** y **POSIBLEMENTE RECICLADA** (más de 3 días o sin fecha). Una duplicada o reciclada siempre se rechaza.
- **Cinco papeles** en la misma llamada: scout, escéptico, cuant, riesgo y revisor final, una línea cada uno en español
  llano. Veredicto: RECHAZAR, VIGILAR o ENSEÑAR AL HUMANO. Las frases que suenan a «compra» o «vende» se borran con el
  mismo filtro que el análisis.
- **Diario de mentira**: una cuenta ficticia de 1.000 $ (`datos/radar/diario.json`). Una idea ENSEÑAR AL HUMANO queda como
  propuesta y solo entra si pulsas «Aprobar (simulación)» (caduca en 3 días). Cada idea usa 100 $ de mentira desde el
  último cierre guardado y se sigue con los cierres diarios de la sala durante las sesiones que dijo; al cerrarse, el
  siguiente radar escribe una lección de una línea (autoevaluación).

Nada de esto compra ni vende, ni es un consejo de inversión.

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

El analista cuantitativo prueba una estrategia contra el histórico de un par con **walk-forward**: elige los parámetros en un tramo de 180 días (dentro de muestra) y los juzga en los 60 días siguientes (fuera de muestra), que nunca ha visto; luego avanza 60 días y repite. Todas las ventanas fuera de muestra se cosen en una sola curva y **las puertas se evalúan solo ahí**. Las rejillas de parámetros viven en el código (4 combinaciones por estrategia); desde la página solo eliges estrategia, par y rango de fechas. Si salta el apagado del −12 %, la simulación cierra la posición y no vuelve a operar en esa ventana; en la siguiente reanuda con el pico puesto en el capital de ese momento (como harías tú tras revisar) y el apagado queda contado: la puerta 7 exige cero. Las entradas al azar del contraste reanudan en los mismos puntos.

Las seis estrategias de corto plazo, todas long-only y al contado: `rotura_dia` (rotura del máximo del día, 15 min, la misma idea que la alerta del vigía), `pico_volumen` (pico de volumen con vela alcista, 5 min, misma definición que la alerta), `cruce_medias` (cruce de medias con stop móvil, 240 min), `donchian` (rotura de canal, 240 min), `bandas` (reversión a la media en tendencia, 60 min) y `rsi` (RSI sobrevendido con filtro de tendencia, 15 min). `python app.py backtest --lista` las enseña, junto con las tres diarias de «Backtest de bolsa».

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
| 7 | Apagados por −12 % | como mucho 1 cada 3 años fuera de muestra, y con el drawdown < 20 % |

Al lado, como referencia y no como puerta: **comprar y mantener** (todo el capital) y **comprar y mantener al 30 %** (lo que permite la regla 5), con los mismos costes, y la distribución de las entradas al azar. Qué significa cada veredicto:

- **PASA las puertas de Fase 1 fuera de muestra**: la estrategia merece meses de paper trading (Fase 2). Nunca dinero real.
- **NO PASA**: la lista dice qué puerta falla y con qué número.
- **INSUFICIENTE**: no hay datos para juzgar; hacen falta al menos 4 ventanas fuera de muestra (420 días de velas), sin huecos de más de 7 días en el rango.

Además salen **avisos** que no bloquean pero que conviene leer: degradación entre dentro y fuera de muestra, profit factor sospechosamente alto, menos de un año fuera de muestra, un rango que no incluye 2022 (año bajista: una estrategia long-only parece buena en alcista), órdenes rechazadas, parámetros por defecto o inestables, y cuántos backtests llevas ya de esa estrategia y par.

Con `cruce_medias` y `donchian` (240 min) hay pocas operaciones por ventana, así que necesitan **años de datos** (4–6) para pasar de INSUFICIENTE o de «pocas operaciones»; con `rsi` y `pico_volumen` espera que los costes pesen. Y la frase que hay que repetirse: **cada backtest es una prueba más; de 20 estrategias sin ventaja real, una aprobaría las puertas por puro azar**. Por eso la página lleva la cuenta de los backtests hechos y exige, antes de creerte una estrategia, que pase en los dos pares y con otro tamaño de ventana.

Cada resultado queda en `datos/backtests/<id>.json` (parámetros por ventana, operaciones, curvas, puertas, referencias, costes) y en el diario de la sala como «idea» del analista cuantitativo. Un backtest tarda entre segundos y varios minutos; desde el panel se hace de uno en uno y comparte el procesador con el vigía: para el contraste completo (200 semillas) y marcos de 5 minutos, mejor la terminal.

## Backtest de bolsa

El mismo walk-forward honrado (las cinco reglas de riesgo y las siete puertas, juzgado solo fuera de muestra) juzga también estrategias de **medio plazo (días a semanas) sobre velas diarias**, en los mercados tradicionales y en BTC/ETH.

**Datos.** `python app.py historico yahoo` baja de Yahoo Finance todas las velas diarias que haya (apertura, máximo, mínimo, cierre y volumen; decenas de años en el S&P 500 y el IBEX, desde 2000 en oro y plata, desde 2007 en Brent, desde 2010 en Tesla) y las guarda en `datos/historico/<CÓDIGO>/1440m/` como velas de 1440 min (fuente «yahoo»), al lado de las de Kraken. Los mercados están en `config.yaml → mercados_backtest`, con un código de par válido para el histórico: `SPX500` (S&P 500), `IBEX35`, `OROUSD` (oro), `PLATAUSD` (plata), `BRENTUSD` (Brent), `TSLAUSD` (Tesla), `NDX100` (Nasdaq 100), `STOXX50` (Euro Stoxx 50), `DAX40EUR` (DAX), `NVDAUSD` (Nvidia), `AAPLUSD` (Apple), `MSFTUSD` (Microsoft), y las cripto de Yahoo en euros `SOLEURY` (Solana), `XRPEURY` (XRP), `BNBEURY` (BNB), `DOGEEURY` (Dogecoin) y `ADAEURY` (Cardano). Índices y empresas pagan los costes de bolsa (0,10 %); las cripto de Yahoo, los de cripto del backtest (0,40 %). Son solo para backtest: el vigía no los mira y nada se opera. La portada fácil sigue con sus 420 cierres de `datos/bolsa/`. Para BTC y ETH diarios con años de datos hace falta el CSV de Kraken importado (ver «Histórico»); la API solo da 720 días.

**Costes por mercado** (`config.yaml → costes`, % por lado): en bolsa, con un bróker barato de la UE, **0,10 % de comisión + 0,05 % de deslizamiento** (0,10 % en stops); cripto sigue con la sección `backtest` (0,40 % taker de Kraken). Un mercado sin entrada en `costes` usa los de `backtest`. Antes de creerte un PASA, repite con tu tarifa real y con el doble de deslizamiento.

**Estrategias diarias** (marco 1440, long-only, rejillas fijadas en el código antes de mirar ningún resultado):

- `donchian_dia`: compra al cerrar por encima del máximo de n días (n = 20 o 55) y sale al perder el mínimo de m días (m = 10 o 20), un stop que solo sube. Cuenta como un intento más de la familia `donchian`.
- `cruce_medias_dia`: cruce dorado de medias simples 50/200 (o 20/100); sale en el cruce contrario, con un stop de protección a 3 ATR. Familia `cruce_medias`. Hay muy pocos cruces: espera NO PASA por número de operaciones (puerta 4) aunque el resultado sea bueno, y es lo honesto.
- `rebote_minimo` (la idea del vídeo): tras una caída, el precio marca un mínimo por debajo del mínimo de oscilación anterior y rebota **más deprisa de lo que cayó** (sesiones de rebote < sesiones de caída) recuperando al menos el 38,2 % o el 50 % de la caída → compra; stop bajo el nuevo mínimo (medio ATR), que luego sube al mínimo de m días (10 o 20); salida a las 40 sesiones si nada salta antes. Pivotes de 3 sesiones a cada lado, una caída de al menos 3 ATR y una entrada por mínimo; los detalles están en el comentario de la clase.

Los intentos se cuentan **por familia y mercado**: `donchian_dia` en el S&P 500 suma a los backtests de `donchian` en el S&P 500, y el aviso lo dice.

**Ventanas en diario: 2 años dentro de muestra / 6 meses fuera** (avanzando 6 meses; la última fuera de muestra vale si tiene al menos 90 días). Con 180/60 días, una estrategia diaria hace de 0 a 3 operaciones por ventana dentro de muestra: no hay con qué elegir parámetros y casi todas las ventanas caerían en «por defecto». Con 2 años hay de 4 a 20 operaciones por combinación (el mínimo para elegir es 4 en `donchian_dia`, 3 en `rebote_minimo`, 1 en `cruce_medias_dia`), y con 6 meses fuera la consistencia (puerta 6) se mide en tramos con alguna operación. Para 4 ventanas hacen falta 4 años de datos. Las siete puertas no cambian: más de 100 operaciones fuera de muestra (en diario eso pide muchos años: el S&P 500 y el IBEX los tienen, Tesla y Brent quizá no), y la puerta 7 permite como mucho un apagado del −12 % por cada 3 años fuera de muestra, con el drawdown < 20 %. Se cambian en `config.yaml → backtest.ventana_is_dias_diario`, `ventana_oos_dias_diario` y `oos_min_dias_diario`; cambiar la ventana después de ver un resultado es otro intento.

**Qué ejecutar en tu PC**: haz doble clic en `backtest_bolsa.bat` (viene en la carpeta del proyecto; esto es lo que hace; tarda de 30 minutos a 2 horas y deja todo en `datos\backtest_bolsa.log`):

```
@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe app.py historico yahoo
.venv\Scripts\python.exe app.py historico
for %%E in (donchian_dia cruce_medias_dia rebote_minimo) do (
  for %%P in (SPX500 IBEX35 NDX100 STOXX50 DAX40EUR OROUSD PLATAUSD BRENTUSD TSLAUSD NVDAUSD AAPLUSD MSFTUSD SOLEURY XRPEURY BNBEURY DOGEEURY ADAEURY XBTEUR ETHEUR) do (
    echo ===== %%E %%P =====>> datos\backtest_bolsa.log
    .venv\Scripts\python.exe app.py backtest %%E --par %%P >> datos\backtest_bolsa.log 2>&1
  )
)
pause
```

**Huecos en los datos diarios.** Si un mercado de bolsa o materias primas tiene un hueco de más de 7 días en Yahoo (el Brent tiene uno de 16 días en abril de 2009), el backtest no rellena el hueco con precios inventados: empieza después del último hueco largo (más el calentamiento de la estrategia) y lo dice en los avisos. En cripto, que cotiza todos los días, un hueco largo es un fallo de datos y sigue dando INSUFICIENTE.

Son 57 backtests (3 estrategias × 19 mercados): 57 intentos más. Con 57 pruebas sin ninguna ventaja real, lo esperable es que **alguna pase por puro azar**; un PASA aislado en un solo mercado no vale nada. Créetelo solo si la misma estrategia pasa en varios mercados parecidos (varios índices, o oro y plata) y sobrevive al doble de deslizamiento.

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
.venv\Scripts\python.exe app.py historico derivar                 rehace las velas de 60 y 1440 min a partir de las de 1 min (tras rellenar un hueco con la API de operaciones)
.venv\Scripts\python.exe app.py backtest ESTRATEGIA               [--par XBTEUR] [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD] [--semillas 200]
.venv\Scripts\python.exe app.py backtest --lista                  las nueve estrategias (seis intradía/4 h y tres diarias)
.venv\Scripts\python.exe app.py historico yahoo [--par SPX500]   histórico diario largo de los mercados de bolsa (ver «Backtest de bolsa»)
.venv\Scripts\python.exe app.py bolsa [--sin-red]                 actualiza los precios de la portada fácil y enseña los semáforos
```

`app.py backtest` devuelve 0 si PASA, 2 si NO PASA, 3 si INSUFICIENTE y 1 si hay un error (por ejemplo, sin histórico). Mientras el panel está abierto, el vigía es el único que escribe en `datos/velas`; `app.py vigilar` es para cuando el panel está cerrado. `app.py historico` y `app.py backtest` sí pueden correr con el panel abierto.

### Qué ejecutar en tu PC la primera vez

Kraken solo es alcanzable desde tu PC: nada del histórico se ha podido probar contra la API real, y la primera ejecución puede descubrir detalles (claves de la respuesta, límites de tasa) que las pruebas sin red no reproducen. Primera tarea: un solo par y leer el registro.

1. `python tests\prueba_backtest.py`, `python tests\prueba.py` y `python tests\prueba_facil.py` (sin red; las tres deben decir «Todo bien.»). Luego `python app.py bolsa` comprueba que Yahoo Finance responde desde tu PC.
2. `python app.py historico --par XBTEUR` (unos 5 minutos de cola OHLC y consolidación; el relleno por Trades de los últimos 90 días tarda 1–1,5 h la primera vez y un minuto al día después). Se puede cortar con Ctrl+C y relanzar.
3. Recomendado: descarga en el navegador el CSV trimestral OHLCVT de Kraken (artículo 360047124832), descomprímelo y `python app.py historico importar D:\descargas\Kraken_OHLCVT --par XBTEUR --par ETHEUR --desde 2019-01-01` (2–5 minutos por par). Después, `python app.py historico` solo rellena el hueco entre el fin del CSV y hoy.
4. Alternativa sin CSV: `python app.py historico --dias 730` (una o dos noches por par, reanudable).
5. `python app.py historico validar --red` (cruza la diaria remuestreada con la diaria de Kraken de los últimos 30 días) y `python app.py comprobar` (sección «Histórico»).
6. `python app.py backtest rotura_dia --par XBTEUR`, o el botón de la página Backtest. Con `rsi` y `pico_volumen` espera que los costes pesen; con `cruce_medias` y `donchian` espera INSUFICIENTE o NO PASA por número de operaciones hasta tener 4–6 años de datos.
7. Ajusta `config.yaml → backtest.comision_pct` a la tarifa real de tu cuenta (kraken.com/features/fee-schedule) y repite con el doble de deslizamiento antes de creerte un PASA.

Con el panel abierto, `app.py historico` puede correr (solo escribe en `datos/historico`), pero no dos a la vez sobre el mismo par (bloqueo `ocupado.json`).

## Estructura

```
app.py                     CLI: panel, vigilar, comprobar, historico, backtest, bolsa, analisis, radar
nucleo.py                  carpetas, config.yaml, .env, informe de comprobación
panel.py                   Flask: páginas, JSON de estado, diario, calendario, interruptores, bucle del vigía, trabajos de backtest e histórico
sala/mercado.py            velas, alertas, parte, calendario, diario
sala/historico.py          histórico de Kraken: OHLC + Trades + CSV, lector de rangos, remuestreo, validación
sala/estrategias.py        indicadores puros, contrato Estrategia, las seis estrategias
sala/backtest.py           motor con las cinco reglas, métricas, walk-forward, referencias, azar, puertas, veredicto
sala/bolsa.py              portada fácil: precios diarios de Yahoo Finance, semáforos, aviso honesto
sala/radar.py              radar de noticias: una llamada a Claude, detector de noticias viejas, cinco papeles, diario de mentira
sala/yahoo.py              histórico diario largo (OHLCV) de Yahoo Finance para el backtest de bolsa
sala/telegram.py           envío de mensajes (sin botones ni escucha)
sala/claude.py             Claude Code en modo headless, opcional (solo para el calendario)
sala/equipo.py             las siete personas y las reglas de riesgo
templates/                 base, facil, sala, oficina, backtest, comprobar
sala/oficina.py            los datos de la oficina visual (departamentos, pantallas, trabajadores)
tests/prueba.py            prueba sin red de la Fase 0: python tests\prueba.py
tests/prueba_facil.py      prueba sin red de la portada fácil (Yahoo Finance falso, caché, colores, aviso, rutas, CLI): python tests\prueba_facil.py
tests/prueba_oficina.py    prueba sin red de la oficina visual (departamentos, pantallas, simulación, rutas): python tests\prueba_oficina.py
tests/prueba_radar.py      prueba sin red del radar (etiquetas, papeles, filtro de consejos, aprobación, cuentas del diario): python tests\prueba_radar.py
tests/prueba_backtest.py   prueba sin red de la Fase 1 (histórico, motor, estrategias, veredicto, página, CLI): python tests\prueba_backtest.py
datos/                     lo que genera la sala (no va al repositorio, salvo la semilla del calendario)
datos/historico/           <par>/<N>m/<AAAA-MM>.json, estado.json, cursor_trades.json, ocupado.json
datos/backtests/           <id>.json e indice.json
```

## Aviso

Esto no es asesoramiento financiero ni una recomendación de compra o venta. La mayoría de quien hace trading a corto plazo pierde dinero. Todo lo que hace esta app es simulación con datos públicos. Cada backtest es una prueba más: de 20 estrategias sin ventaja real, una aprobaría las puertas por puro azar.
