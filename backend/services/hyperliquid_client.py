import traceback
import asyncio
import time
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
        
        # Lightweight API caching to prevent uvicorn event loop blocks & rate-limit exhausts
        self._user_state_cache = None
        self._user_state_cache_time = 0.0
        self._open_orders_cache = None
        self._open_orders_cache_time = 0.0
        
        # Throttling & Rate Limit States
        self.is_throttled = False
        self.throttle_release_time = 0.0
        
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

    async def get_user_state(self):
        """Fetch general balance, leverage, and margin details with caching."""
        if not self.is_active or not self.wallet_address:
            # Fallback mock state for visual dashboard demo
            return {
                "marginSummary": {"accountValue": "10000.0", "totalMarginUsed": "0.0", "withdrawable": "10000.0"},
                "assetPositions": [],
                "is_mock": True
            }
        
        now = time.time()
        # Cache user state for 2.5 seconds to prevent network spam and save rate limits
        if self._user_state_cache is not None and now - self._user_state_cache_time < 2.5:
            return self._user_state_cache
            
        try:
            target_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
            user_state = await asyncio.to_thread(self.info.user_state, target_user)
            self._user_state_cache = user_state
            self._user_state_cache_time = now
            return user_state
        except Exception as e:
            db.log_system("ERROR", f"Error fetching user state: {str(e)}. Using stale cache fallback.")
            if self._user_state_cache is not None:
                return self._user_state_cache
            return None

    async def get_positions(self, user_state=None):
        """Fetch currently active perpetual positions.
        Accepts optional pre-fetched user_state to avoid redundant API calls.
        """
        state = user_state if user_state is not None else await self.get_user_state()
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

    def _handle_cumulative_limit_error(self, coin: str):
        """
        Handles the Hyperliquid Cumulative Limit Error by applying a 10.5s backoff
        and then executing a taker order to regenerate the L1 request quota.
        """
        if getattr(self, 'is_throttled', False):
            return
            
        self.is_throttled = True
        self.throttle_release_time = time.time() + 10.5
        db.log_system("CRITICAL", "CUMULATIVE LIMIT DEFICIT DETECTED! API Throttled for 10.5 seconds. Initiating Recovery Protocol.")
        
        # We need to spawn this as a background task so we don't block the caller
        async def recovery_task():
            db.log_system("SYSTEM", f"Recovery Task: Sleeping for 10.5s before taking action...")
            await asyncio.sleep(10.5)
            
            # The user requested a Taker order to unblock.
            recovery_coin = coin if coin else "SUI"
            try:
                # Fetch price
                from backend.services.orderbook_tracker import tracker
                from backend.services.risk_manager import risk_manager
                
                market_state = tracker.get_market_state(recovery_coin)
                mid_px = market_state.get("mid", 0.0)
                
                if mid_px > 0:
                    decimals, min_size = risk_manager.get_asset_sz_decimals(recovery_coin)
                    
                    # We need $11 minimum notional
                    import math
                    required_size = 11.0 / mid_px
                    step = 10 ** (-decimals) if decimals > 0 else 1.0
                    steps = math.ceil(required_size / step)
                    rounded_size = round(steps * step, decimals)
                    
                    # Execute Taker BUY (Market Order logic via slippage)
                    exec_price = float(f"{mid_px * 1.05:.5g}")  # 5% slippage to guarantee Taker execution
                    
                    db.log_system("RECOVERY", f"Placing Recovery Taker BUY for {recovery_coin}. Size: {rounded_size}")
                    
                    # Bypass the local throttle check for this specific recovery order
                    self.is_throttled = False 
                    
                    order_result = await asyncio.to_thread(
                        self.exchange.order,
                        recovery_coin,
                        True,  # is_buy
                        rounded_size,
                        exec_price,
                        {"limit": {"tif": "Ioc"}},  # Immediate-or-Cancel
                        reduce_only=False
                    )
                    
                    db.log_system("RECOVERY", f"Recovery Buy Result: {order_result}")
                    
                    if isinstance(order_result, dict) and order_result.get("status") == "ok":
                        # Immediately close the position to flat
                        await asyncio.sleep(1.0)
                        sell_price = float(f"{mid_px * 0.95:.5g}") # 5% slippage down
                        db.log_system("RECOVERY", f"Closing Recovery Position for {recovery_coin}")
                        
                        close_result = await asyncio.to_thread(
                            self.exchange.order,
                            recovery_coin,
                            False,  # is_buy=False (Sell)
                            rounded_size,
                            sell_price,
                            {"limit": {"tif": "Ioc"}},
                            reduce_only=True
                        )
                        db.log_system("RECOVERY", f"Recovery Close Result: {close_result}")
                    else:
                        db.log_system("ERROR", f"Recovery BUY failed (likely insufficient margin). Aborting Taker SELL to prevent Naked Short!")
                    
                else:
                    db.log_system("ERROR", f"Could not get price for {recovery_coin} during recovery.")
                    self.is_throttled = False
            except Exception as e:
                db.log_system("ERROR", f"Recovery protocol failed: {str(e)}")
                self.is_throttled = False
                
            db.log_system("SYSTEM", "Recovery Protocol Complete. Normal operations resumed.")

        # Run background task
        asyncio.create_task(recovery_task())

    async def place_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False):
        """
        Place an order to Hyperliquid L1.
        If AGENT_PRIVATE_KEY is missing, executes a local mock simulation trade.
        """
        # 1. Throttling Pre-Check
        if getattr(self, 'is_throttled', False) and time.time() < getattr(self, 'throttle_release_time', 0.0):
            return {"status": "error", "message": "Local Throttle: Bot is in 10.5s cooldown recovery mode"}
        elif getattr(self, 'is_throttled', False) and time.time() >= getattr(self, 'throttle_release_time', 0.0):
            self.is_throttled = False

        if not self.is_active:
            db.log_system("SIMULATION", f"PLACING MOCK ORDER: {coin} | Side: {'BUY/LONG' if is_buy else 'SELL/SHORT'} | Size: {size} | Px: {price}")
            db.record_trade(coin, "BUY" if is_buy else "SELL", size, price, pnl=0.0, cloid="MOCK_TX")
            return {"status": "ok", "response": {"type": "mock", "id": "MOCK_ORDER_ID"}}
            
        try:
            # Always ensure dynamic leverage is optimal for small accounts before placing first order
            try:
                user_state = await self.get_user_state()
                account_val = 0.0
                if user_state:
                    account_val = float(user_state.get("crossMarginSummary", {}).get("accountValue", "0.0"))
                    if account_val == 0.0:
                        account_val = 100.0
                
                if account_val < 50.0:
                    leverage_to_set = 50
                elif account_val < 500.0:
                    leverage_to_set = 20
                else:
                    leverage_to_set = 5
                    
                await asyncio.to_thread(self.exchange.update_leverage, leverage_to_set, coin)
            except Exception as le:
                db.log_system("WARNING", f"Could not adjust leverage dynamically for {coin}: {str(le)}")

            # 5 significant figures for price as required by Hyperliquid
            rounded_price = float(f"{price:.5g}")
            
            # Use RiskManager for dynamic size rounding
            from backend.services.risk_manager import risk_manager
            decimals, min_size = risk_manager.get_asset_sz_decimals(coin)
            rounded_size = round(size, decimals)
            
            db.log_system("EXECUTION", f"Sending L1 Tx: {coin} | Buy: {is_buy} | Size: {rounded_size} | Px: {rounded_price} | ReduceOnly: {reduce_only}")
            
            # Place order via Hyperliquid Exchange API
            order_result = await asyncio.to_thread(
                self.exchange.order,
                coin,
                is_buy,
                rounded_size,
                rounded_price,
                {"limit": {"tif": "Gtc"}},
                reduce_only=reduce_only
            )
            
            db.log_system("INFO", f"Exchange order response: {order_result}")
            
            # 2. Cumulative Limit Interceptor
            if order_result.get("status") == "err":
                err_resp = str(order_result.get("response", ""))
                if "Too many cumulative requests" in err_resp:
                    self._handle_cumulative_limit_error(coin)
            
            if order_result.get("status") == "ok":
                # Log to local trade history
                db.record_trade(coin, "BUY" if is_buy else "SELL", rounded_size, rounded_price, pnl=0.0, cloid="L1_TX")
                
            return order_result
            
        except Exception as e:
            db.log_system("ERROR", f"Failed to place order on Hyperliquid: {str(e)}")
            import traceback
            db.log_system("DEBUG", traceback.format_exc())
            return {"status": "error", "message": str(e)}

    async def get_open_orders(self, coin: str = None):
        """Fetch active resting limit orders with caching to prevent API spam."""
        if not self.is_active:
            return []
            
        now = time.time()
        # Cache open orders for 2.5 seconds to prevent network spam and save rate limits
        if self._open_orders_cache is not None and now - self._open_orders_cache_time < 2.5:
            open_orders = self._open_orders_cache
        else:
            try:
                target_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
                open_orders = await asyncio.to_thread(self.info.open_orders, target_user)
                self._open_orders_cache = open_orders
                self._open_orders_cache_time = now
            except Exception as e:
                db.log_system("ERROR", f"Error fetching open orders: {str(e)}. Using stale cache fallback.")
                if self._open_orders_cache is not None:
                    open_orders = self._open_orders_cache
                else:
                    return []
            
        if coin:
            return [o for o in open_orders if o.get("coin") == coin]
        return open_orders

    async def cancel_order(self, coin: str, oid: int):
        """Cancel a specific resting limit order."""
        # 1. Throttling Pre-Check
        if getattr(self, 'is_throttled', False) and time.time() < getattr(self, 'throttle_release_time', 0.0):
            return {"status": "error", "message": "Local Throttle: Bot is in 10.5s cooldown recovery mode"}
        elif getattr(self, 'is_throttled', False) and time.time() >= getattr(self, 'throttle_release_time', 0.0):
            self.is_throttled = False

        if not self.is_active:
            db.log_system("SIMULATION", f"MOCK CANCEL ORDER: {coin} | OID: {oid}")
            return {"status": "ok"}
            
        try:
            db.log_system("EXECUTION", f"Cancelling L1 Order: {coin} | OID: {oid}")
            cancel_result = await asyncio.to_thread(self.exchange.cancel, coin, oid)
            db.log_system("INFO", f"Cancel order response: {cancel_result}")
            
            # Cumulative Limit Interceptor
            if isinstance(cancel_result, dict) and cancel_result.get("status") == "err":
                err_resp = str(cancel_result.get("response", ""))
                if "Too many cumulative requests" in err_resp:
                    self._handle_cumulative_limit_error(coin)
                    
            return cancel_result
        except Exception as e:
            db.log_system("ERROR", f"Error cancelling order {oid} for {coin}: {str(e)}")
            return False

    async def cancel_all_orders(self):
        """Emergency Kill Switch - cancels all open orders."""
        if not self.is_active:
            db.log_system("SIMULATION", "Emergency Stop: Cancelling all simulation orders.")
            return True
            
        try:
            db.log_system("EMERGENCY", "INVOLKING EMERGENCY KILL SWITCH: Cancelling all open orders!")
            # Get open orders
            target_user = Config.ACCOUNT_ADDRESS if Config.ACCOUNT_ADDRESS else self.wallet_address
            open_orders = await asyncio.to_thread(self.info.open_orders, target_user)
            
            for order in open_orders:
                await asyncio.to_thread(self.exchange.cancel, order["coin"], order["oid"])
                
            db.log_system("INFO", "All open orders successfully cancelled.")
            return True
        except Exception as e:
            db.log_system("ERROR", f"Error executing emergency cancellation: {str(e)}")
            return False

hl_client = HyperliquidClient()

