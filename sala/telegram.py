"""Minimal Telegram bot: it only sends text (alerts and the daily report). No polling, no buttons."""
import requests

import nucleo


class Bot:
    def __init__(self, token=None, chat=None):
        if token is None and chat is None:
            token, chat = nucleo.telegram_credenciales()
        self.token = (token or "").strip()
        self.chat = str(chat or "").strip()
        self.activo = bool(self.token and self.chat)

    def _api(self, metodo, **kwargs):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{metodo}", timeout=kwargs.pop("timeout", 60), **kwargs)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}

    def texto(self, mensaje):
        """Send a message; True unless Telegram refused it (False) or the bot is not configured (None)."""
        if not self.activo:
            return None
        try:
            return bool(self._api("sendMessage", data={"chat_id": self.chat, "text": mensaje[:4000]}).get("ok"))
        except (requests.RequestException, ValueError):
            return False

    def comprobar(self, inf):
        inf.seccion("Telegram")
        if not self.token:
            inf.falta("TELEGRAM_TOKEN", "(en .env; sin él no hay alertas ni parte, pero la sala funciona)")
            return
        try:
            r = self._api("getMe", timeout=nucleo.TIMEOUT)
        except (requests.RequestException, ValueError) as err:
            inf.error("Telegram bot", f"(sin conexión: {type(err).__name__})")
            return
        if r.get("ok"):
            inf.ok("Telegram bot", f"(@{(r.get('result') or {}).get('username')})")
        else:
            inf.error("Telegram bot", "(token no válido)")
            return
        if not self.chat:
            inf.falta("TELEGRAM_CHAT")
            return
        try:
            r = self._api("getChat", data={"chat_id": self.chat}, timeout=nucleo.TIMEOUT)
        except (requests.RequestException, ValueError) as err:
            inf.error("Telegram chat", f"(sin conexión: {type(err).__name__})")
            return
        inf.ok("Telegram chat") if r.get("ok") else inf.error("Telegram chat", "(escribe algo a tu bot primero y revisa el ID)")
