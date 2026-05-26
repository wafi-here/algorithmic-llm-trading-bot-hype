import traceback
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from backend.config import Config
from backend.services.database import db

class HyperliquidClient:
    def __init__(self):
        self.is_active = False
        self.info = None
        self.exchange = None
        self.wallet_address = None
        
        # Determine network constant
        self.base_url = Config.API_URL
        
        try:
            db.log_system("INFO", f"Initializing Hyperliquid client on {self.base_url}")
            self.info = Info(self.base_url, skip_ws=True)
            
            if Config.AGENT_PRIVATE_KEY:
                # Load agent account
                self.account = Account.from_key(Config.AGENT_PRIVATE_KEY)
                self.wallet_address = self.account.address
                
                # Setup Exchange connection
                # If ACCOUNT_ADDRESS is specified, it represents the master wallet,
                # and this client acts as an Agent executing on behalf of master.
                margin_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
                self.exchange = Exchange(self.account, self.base_url, account_address=margin_user)
                
                self.is_active = True
                db.log_system("INFO", f"Hyperliquid Engine authenticated successfully! Active Agent: {self.wallet_address}")
                if Config.ACCOUNT_ADDRESS:
                    db.log_system("INFO", f"Trading on behalf of master wallet: {Config.ACCOUNT_ADDRESS}")
            else:
                db.log_system("WARNING", "No AGENT_PRIVATE_KEY provided in .env! Running in READ-ONLY & SIMULATION mode.")
                self.is_active = False
                
        except Exception as e:
            db.log_system("ERROR", f"Failed to initialize Hyperliquid SDK client: {str(e)}")
            db.log_system("DEBUG", traceback.format_exc())
            self.is_active = False

    def get_user_state(self):
        """Fetch general balance, leverage, and margin details."""
        if not self.is_active or not self.wallet_address:
            # Fallback mock state for visual dashboard demo
            return {
                "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0", "withdrawable": "10000.0"},
                "assetPositions": [],
                "is_mock": True
            }
        
        try:
            target_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
            user_state = self.info.user_state(target_user)
            return user_state
        except Exception as e:
            db.log_system("ERROR", f"Error fetching user state: {str(e)}")
            return None

    def get_positions(self):
        """Fetch currently active perpetual positions."""
        state = self.get_user_state()
        if not state:
            return []
        
        positions = []
        # In Hyperliquid response, positions are under 'assetPositions'
        asset_positions = state.get("assetPositions", [])
        for pos in asset_positions:
            p = pos.get("position", {})
            if float(p.get("szi", 0)) != 0:
                positions.append({
                    "coin": p.get("coin"),
                    "side": "LONG" if float(p.get("szi", 0)) > 0 else "SHORT",
                    "size": abs(float(p.get("szi", 0))),
                    "entry_px": float(p.get("entryPx", 0)),
                    "liquidation_px": float(p.get("liquidationPx", 0)),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0)),
                    "leverage": p.get("leverage", {}).get("value", 1)
                })
        return positions

    def place_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False):
        """
        Place an order to Hyperliquid L1.
        If AGENT_PRIVATE_KEY is missing, executes a local mock simulation trade.
        """
        if not self.is_active:
            db.log_system("SIMULATION", f"PLACING MOCK ORDER: {coin} | Side: {'BUY/LONG' if is_buy else 'SELL/SHORT'} | Size: {size} | Px: {price}")
            db.record_trade(coin, "BUY" if is_buy else "SELL", size, price, pnl=0.0, cloid="MOCK_TX")
            return {"status": "ok", "response": {"type": "mock", "id": "MOCK_ORDER_ID"}}

        try:
            # Set leverage to 3x default for risk management safety
            try:
                self.exchange.update_leverage(3, coin)
            except Exception:
                pass # Already set or handled

            # Round size and price according to Hyperliquid specifications
            # We round size and price to 4 decimal places as a general baseline
            rounded_size = round(size, 4)
            rounded_price = round(price, 4)
            
            db.log_system("EXECUTION", f"Sending L1 Tx: {coin} | Buy: {is_buy} | Size: {rounded_size} | Px: {rounded_price} | ReduceOnly: {reduce_only}")
            
            # Place order via Hyperliquid Exchange API
            # limit_px, is_buy, size, order_type (e.g. {'limit': {'tif': 'Gtc'}})
            order_result = self.exchange.order(
                coin,
                is_buy,
                rounded_size,
                rounded_price,
                {"limit": {"tif": "Gtc"}},
                reduce_only=reduce_only
            )
            
            db.log_system("INFO", f"Exchange order response: {order_result}")
            
            if order_result.get("status") == "ok":
                # Log to local trade history
                db.record_trade(coin, "BUY" if is_buy else "SELL", rounded_size, rounded_price, pnl=0.0, cloid="L1_TX")
                
            return order_result
            
        except Exception as e:
            db.log_system("ERROR", f"Failed to place order on Hyperliquid: {str(e)}")
            db.log_system("DEBUG", traceback.format_exc())
            return {"status": "error", "message": str(e)}

    def cancel_all_orders(self):
        """Emergency Kill Switch - cancels all open orders."""
        if not self.is_active:
            db.log_system("SIMULATION", "Emergency Stop: Cancelling all simulation orders.")
            return True
            
        try:
            db.log_system("EMERGENCY", "INVOLKING EMERGENCY KILL SWITCH: Cancelling all open orders!")
            # Get open orders
            target_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
            open_orders = self.info.open_orders(target_user)
            
            for order in open_orders:
                self.exchange.cancel(order["coin"], order["oid"])
                
            db.log_system("INFO", "All open orders successfully cancelled.")
            return True
        except Exception as e:
            db.log_system("ERROR", f"Error executing emergency cancellation: {str(e)}")
            return False

hl_client = HyperliquidClient()
