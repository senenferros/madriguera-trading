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
