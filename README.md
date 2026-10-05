# La Madriguera Trading

Una sala de mercados pequeña y honesta: un vigía que lee los precios públicos de Bitcoin y Ethereum en Kraken cada minuto, guarda el histórico en tu PC, avisa por Telegram cuando pasa algo, mantiene un calendario macro y lleva el diario de operaciones simuladas. **Simulación · datos públicos · sin dinero real.** No hay claves de exchange, no hay órdenes, no hay nada que pueda comprar o vender.

Es un proyecto independiente, extraído del departamento de mercados que vivía dentro de la oficina de Shorts. Aquí no hay vídeos ni YouTube: solo la sala.

## Qué hace hoy (Fase 0)

- **Vigía**: velas de un minuto de `XBTEUR` y `ETHEUR` (API pública de Kraken), guardadas en `datos/velas/<par>/<día>.json`. Si arranca a media mañana, rellena la parte del día que falta con velas de 5 minutos, para que la apertura y el rango sean los del día real.
- **Alertas**: rotura del máximo o del mínimo del día (una vez al día por lado, tras 30 velas) y picos de volumen (×3 sobre la mediana de la última hora, con 30 minutos de enfriamiento). Se guardan en `datos/alertas.json` y, si hay Telegram, se envían.
- **Parte diario** por Telegram a la hora de `config.yaml` (08:00 por defecto): precios, rango del día, alertas de las últimas 24 h y agenda macro de hoy y mañana. Si el envío falla, se reintenta al minuto siguiente.
- **Calendario macro**: una semilla con las reuniones de la Fed y del BCE (`datos/calendario_semilla.json`, va en el repositorio) que el documentalista amplía con Claude Code y búsqueda web cuando se lo encargas desde el panel. Si no tienes el comando `claude`, el panel lo dice y el calendario se queda con la semilla y lo que apuntes a mano en `datos/calendario.json`.
- **Diario de la sala**: notas, ideas y operaciones simuladas, con par, precio y cantidad. No se borra nada.
- **Panel web** en `http://127.0.0.1:5100`, solo accesible desde tu propio PC: la sala, la oficina (rótulo de cotizaciones, tres pantallas con los cierres de hoy, la pizarra con las reglas y las siete personas del equipo) y la página de comprobación.

## Las fases

| Fase | Qué | Dinero |
|------|-----|--------|
| **0** | La sala y el vigía: datos, alertas, calendario, diario. Esto es lo que hay ahora. | Ninguno |
| **1** | Backtesting: el analista cuantitativo prueba estrategias contra el histórico guardado en `datos/velas/`. | Ninguno |
| **2** | Paper trading: el operador en simulación ejecuta las estrategias con precios reales y dinero ficticio; el gestor de riesgo aplica las reglas. | Ninguno |
| **3** | Dinero pequeño en exchanges regulados bajo MiCA, solo con lo que la Fase 2 haya aguantado durante meses. El contable lleva el FIFO. | Pequeño |
| **4** | Canal: el cronista cuenta lo que hace la sala, con sus datos y llamando simulación a la simulación. | — |

## Las cinco reglas de riesgo

1. **1 % de riesgo por operación.**
2. **Siempre con stop.**
3. **−3 % en el día: se para.**
4. **−12 % acumulado: se apaga todo.**
5. **Máximo 30 % en un mismo activo.**

Están en la pizarra de la oficina y las aplica el gestor de riesgo cuando haya operaciones (Fases 2 y 3).

## El equipo

Siete personas con nombre propio (se generan la primera vez y quedan en `datos/equipo.yaml`, donde puedes cambiarlos): vigía de mercado, analista cuantitativo, operador en simulación, gestor de riesgo, documentalista de datos, contable y cronista de mercados.

## Cómo arrancarlo en Windows

Necesitas Python 3.11 (marca «Add python.exe to PATH» al instalarlo). Descomprime o clona el proyecto en `D:\LA_MADRIGUERA_TRADING`.

1. Copia `.env.example` como `.env` y rellena `TELEGRAM_TOKEN` (el de BotFather) y `TELEGRAM_CHAT` (tu chat con el bot). Si lo dejas vacío, la sala funciona igual, sin avisos.
2. Haz doble clic en **`Abrir panel.bat`**: crea el entorno `.venv` si no existe, instala lo que haga falta y abre el panel en el navegador. Cierra esa ventana para apagarlo.
3. Revisa `config.yaml` si quieres otros pares, otra hora del parte o cambiar los umbrales de las alertas.

Desde una terminal, en la carpeta del proyecto:

```
.venv\Scripts\python.exe app.py              el panel (y el vigía) en http://127.0.0.1:5100
.venv\Scripts\python.exe app.py vigilar      una pasada del vigía y el parte del día, por pantalla
.venv\Scripts\python.exe app.py comprobar    Kraken, velas de hoy, Telegram y Claude Code
```

Mientras el panel está abierto, el vigía es el único que escribe en `datos/`; `app.py vigilar` es para cuando el panel está cerrado.

## Estructura

```
app.py              CLI: panel, vigilar, comprobar
nucleo.py           carpetas, config.yaml, .env, informe de comprobación
panel.py            Flask: páginas, JSON de estado, diario, calendario, interruptores, bucle del vigía
sala/mercado.py     velas, alertas, parte, calendario, diario
sala/telegram.py    envío de mensajes (sin botones ni escucha)
sala/claude.py      Claude Code en modo headless, opcional (solo para el calendario)
sala/equipo.py      las siete personas y las reglas de riesgo
templates/          base, sala, oficina, comprobar
tests/prueba.py     prueba sin red: python tests\prueba.py
datos/              lo que genera la sala (no va al repositorio, salvo la semilla del calendario)
```

## Aviso

Esto no es asesoramiento financiero ni una recomendación de compra o venta. La mayoría de quien hace trading a corto plazo pierde dinero. Todo lo que hace esta app es simulación con datos públicos.
