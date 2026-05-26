import httpx
import traceback
from backend.config import Config
from backend.services.database import db

class TelegramAlertBroker:
    def __init__(self):
        self.bot_token = ""
        self.chat_id = ""
        self._client = None
        self.load_config()

    def load_config(self):
        # We can dynamically set or reload these config fields
        self.bot_token = getattr(Config, "TELEGRAM_BOT_TOKEN", "")
        self.chat_id = getattr(Config, "TELEGRAM_CHAT_ID", "")

    def get_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)
        return self._client

    async def send_alert(self, message: str) -> bool:
        """Sends a markdown formatted alert message to configured Telegram Channel."""
        db.log_system("ALERT", f"Queuing push alert: {message[:100]}...")
        
        if not self.bot_token or not self.chat_id:
            db.log_system("ALERT_SIM", f"[SIMULATION ALERT] (Telegram Credentials missing): {message}")
            return True
            
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": f"🚨 *Hyperliquid Quant Alert*\n\n{message}",
            "parse_mode": "Markdown"
        }
        
        client = self.get_client()
        try:
            res = await client.post(url, json=payload)
            if res.status_code == 200:
                db.log_system("INFO", "Push alert successfully dispatched to Telegram API.")
                return True
            else:
                db.log_system("WARNING", f"Telegram API returned status: {res.status_code} - {res.text}")
        except Exception as e:
            db.log_system("WARNING", f"Failed to dispatch Telegram message: {str(e)}")
            
        return False

alert_broker = TelegramAlertBroker()
