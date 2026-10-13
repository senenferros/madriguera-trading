' La Madriguera Trading: starts the panel hidden (no console window).
' Used by the shortcut in the Windows Startup folder; double-click it to start the panel by hand.
' Output goes to datos\panel_salida.log and datos\panel_error.log. To stop it: apagar_panel.bat.
Option Explicit

Dim fso, sh, carpeta, env, http, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = carpeta

' If the panel already answers on port 5100, do not start a second one.
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 2000, 2000, 2000, 2000
http.Open "GET", "http://127.0.0.1:5100/", False
http.Send
If Err.Number = 0 Then
  If http.Status > 0 Then WScript.Quit 0
End If
Err.Clear
On Error GoTo 0

If Not fso.FileExists(carpeta & "\.venv\Scripts\pythonw.exe") Then
  MsgBox "No encuentro .venv\Scripts\pythonw.exe. Abre una vez 'Abrir panel.bat' para crear el entorno.", 48, "La Madriguera Trading"
  WScript.Quit 1
End If
If Not fso.FolderExists(carpeta & "\datos") Then fso.CreateFolder carpeta & "\datos"

Set env = sh.Environment("Process")
env("PYTHONUNBUFFERED") = "1"
env("PYTHONIOENCODING") = "utf-8"

cmd = "cmd /c "".venv\Scripts\pythonw.exe app.py panel --sin-navegador" & _
      " >> datos\panel_salida.log 2>> datos\panel_error.log"""
sh.Run cmd, 0, False
