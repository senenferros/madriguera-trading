"""Run Claude Code headless (uses the Claude subscription login, not an API key). Optional: only the calendar refresh
needs it, and the app says so when the `claude` command is missing."""
import json
import os
import shutil
import subprocess

import nucleo


class ErrorClaude(RuntimeError):
    pass


def disponible():
    return bool(shutil.which("claude"))


def preguntar(prompt, schema, herramientas=None, timeout=900):
    """Send one prompt to `claude -p` and return (structured JSON answer, cost in USD).

    herramientas: list of Claude Code tool names the agent may use (e.g. ["WebSearch"]); none by default.
    """
    exe = shutil.which("claude")
    if not exe:
        raise ErrorClaude("No encuentro Claude Code (comando 'claude') en el PATH.")
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        # Keep the agent lean: no user hooks, plugins or MCP servers from the personal setup
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--tools", ",".join(herramientas) if herramientas else "",
    ]
    if herramientas:
        # Headless runs cannot ask for approval, so pre-approve exactly the tools offered
        cmd += ["--allowedTools", ",".join(herramientas)]
    # Explicit environment: force the subscription login and keep the app's own secrets away from the agent
    ocultas = {"ANTHROPIC_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT", *nucleo.cargar_env().keys()}
    env = {k: v for k, v in os.environ.items() if k not in ocultas}
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, env=env, cwd=nucleo.ROOT)
    try:
        datos = json.loads(proc.stdout)
    except ValueError:
        raise ErrorClaude(f"Claude Code no devolvió JSON (código {proc.returncode}): {proc.stderr.strip()[:300]}")
    if datos.get("is_error") or datos.get("structured_output") is None:
        raise ErrorClaude(f"Claude Code falló: {str(datos.get('result'))[:300]}")
    return datos["structured_output"], datos.get("total_cost_usd", 0)
