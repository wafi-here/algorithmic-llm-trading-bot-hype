import traceback
import asyncio
import time
from collections import deque
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from backend.config import Config
from backend.services.database import db

class RateLimitBudget:
    """
    Proactive API rate limit budget tracker using a rolling 60-second window.
    Prevents cumulative limit errors by self-throttling before hitting exchange limits.
    
    Hyperliquid allows ~1200 weight per minute for REST.
    Soft limit (warning) at 80% and hard limit (block) at 95%.
    """
    def __init__(self, max_weight_per_minute: int = 1200):
        self.max_weight = max_weight_per_minute
        self.soft_limit_pct = 0.80
        self.hard_limit_pct = 0.95
        self._request_log = deque()  # Stores (timestamp, weight) tuples
    
    def _prune_old_entries(self):
        """Remove entries older than 60 seconds."""
        cutoff = time.time() - 60.0
        while self._request_log and self._request_log[0][0] < cutoff:
            self._request_log.popleft()
    
    def get_current_usage(self) -> int:
        """Returns total weight used in the current 60-second window."""
        self._prune_old_entries()
        return sum(w for _, w in self._request_log)
    
    def can_send(self, weight: int = 1) -> bool:
        """Checks if the budget allows a request of the given weight."""
        self._prune_old_entries()
        current = self.get_current_usage()
        hard_limit = self.max_weight * self.hard_limit_pct
        return (current + weight) <= hard_limit
    
    def is_approaching_limit(self) -> bool:
        """Returns True if usage is above the soft limit (80%)."""
        current = self.get_current_usage()
        return current >= (self.max_weight * self.soft_limit_pct)
    
    def record_request(self, weight: int = 1):
        """Logs a request timestamp and weight."""
        self._request_log.append((time.time(), weight))
    
    def get_remaining(self) -> int:
        """Returns remaining budget in the current window."""
        return max(0, self.max_weight - self.get_current_usage())

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
        
        # F5: Track which coins have had leverage set to avoid redundant API calls
        self._leverage_set: dict[str, int] = {}
        
        # Throttling & Rate Limit States
        self.is_throttled = False
        self.throttle_release_time = 0.0
        
        # Proactive rate limit budget tracker
        self.rate_budget = RateLimitBudget()
        
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
            # Check rate budget before making API call
            if not self.rate_budget.can_send(weight=2):
                db.log_system("RATE_LIMIT", "Rate budget exhausted for user_state. Using stale cache.")
                if self._user_state_cache is not None:
                    return self._user_state_cache
                return None
            self.rate_budget.record_request(weight=2)
            user_state = await asyncio.to_thread(self.info.user_state, target_user)
            
            # Fetch spot user state to get true Unified Account balance (USDC)
            # We do this every 5 seconds to limit rate usage, reusing cache in between
            now_spot = time.time()
            if not hasattr(self, '_spot_state_cache'):
                self._spot_state_cache = None
                self._spot_state_time = 0
                
            if now_spot - self._spot_state_time > 5.0 and self.rate_budget.can_send(weight=2):
                self.rate_budget.record_request(weight=2)
                try:
                    spot_state = await asyncio.to_thread(self.info.spot_user_state, target_user)
                    self._spot_state_cache = spot_state
                    self._spot_state_time = now_spot
                except Exception:
                    pass
            
            # Inject unified spot balance into user_state if it's higher
            if self._spot_state_cache and "balances" in self._spot_state_cache:
                usdc_balance = 0.0
                for bal in self._spot_state_cache["balances"]:
                    if bal.get("coin") == "USDC":
                        usdc_balance = float(bal.get("total", 0.0))
                        break
                
                # Check cross and isolated, update accountValue if usdc_balance is larger
                cross_val = float(user_state.get("crossMarginSummary", {}).get("accountValue", 0.0))
                iso_val = float(user_state.get("marginSummary", {}).get("accountValue", 0.0))
                
                if usdc_balance > max(cross_val, iso_val):
                    if "crossMarginSummary" in user_state:
                        user_state["crossMarginSummary"]["accountValue"] = str(usdc_balance)
                    if "marginSummary" in user_state:
                        user_state["marginSummary"]["accountValue"] = str(usdc_balance)
            
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
            szi_val = p.get("szi") or 0
            if float(szi_val) != 0:
                positions.append({
                    "coin": p.get("coin"),
                    "side": "LONG" if float(szi_val) > 0 else "SHORT",
                    "size": abs(float(szi_val)),
                    "entry_px": float(p.get("entryPx") or 0),
                    "liquidation_px": float(p.get("liquidationPx") or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                    "leverage": (p.get("leverage") or {}).get("value", 1)
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

    async def place_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False, order_type: str = "Ioc"):
        """
        Place an order to Hyperliquid L1.
        If AGENT_PRIVATE_KEY is missing, executes a local mock simulation trade.
        
        P2: order_type defaults to 'Ioc' (Immediate-or-Cancel) for signal-driven entries.
        Use 'Gtc' (Good-Til-Cancelled) only for grid/MM resting limit orders.
        """
        # 1. Throttling Pre-Check
        if getattr(self, 'is_throttled', False) and time.time() < getattr(self, 'throttle_release_time', 0.0):
            return {"status": "error", "message": "Local Throttle: Bot is in 10.5s cooldown recovery mode"}
        elif getattr(self, 'is_throttled', False) and time.time() >= getattr(self, 'throttle_release_time', 0.0):
            self.is_throttled = False

        if not self.is_active:
            # F3: Entry trades recorded with pnl=None to distinguish from breakeven exits
            trade_pnl = 0.0 if reduce_only else None
            db.log_system("SIMULATION", f"PLACING MOCK ORDER: {coin} | Side: {'BUY/LONG' if is_buy else 'SELL/SHORT'} | Size: {size} | Px: {price} | TIF: {order_type}")
            db.record_trade(coin, "BUY" if is_buy else "SELL", size, price, pnl=trade_pnl, cloid="MOCK_TX")
            return {"status": "ok", "response": {"type": "mock", "id": "MOCK_ORDER_ID"}}
        
        # Rate budget pre-check for live orders
        if not self.rate_budget.can_send(weight=5):
            db.log_system("RATE_LIMIT", f"Rate budget exhausted. Deferring order for {coin}.")
            return {"status": "error", "message": "Rate limit budget exhausted, order deferred"}
        
        if self.rate_budget.is_approaching_limit():
            remaining = self.rate_budget.get_remaining()
            db.log_system("RATE_LIMIT", f"Approaching rate limit. Remaining budget: {remaining} weight. Proceeding cautiously.")
            
        try:
            # F5: Only set leverage when it hasn't been set for this coin or when tier changes
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
                
                # F5: Only call update_leverage if not already set to this value for this coin
                if self._leverage_set.get(coin) != leverage_to_set:
                    self.rate_budget.record_request(weight=2)
                    await asyncio.to_thread(self.exchange.update_leverage, leverage_to_set, coin)
                    self._leverage_set[coin] = leverage_to_set
                    db.log_system("INFO", f"Leverage set to {leverage_to_set}x for {coin}")
            except Exception as le:
                db.log_system("WARNING", f"Could not adjust leverage dynamically for {coin}: {str(le)}")

            # 5 significant figures for price as required by Hyperliquid
            rounded_price = float(f"{price:.5g}")
            
            # Use RiskManager for dynamic size rounding
            from backend.services.risk_manager import risk_manager
            decimals, min_size = risk_manager.get_asset_sz_decimals(coin)
            rounded_size = round(size, decimals)
            
            db.log_system("EXECUTION", f"Sending L1 Tx: {coin} | Buy: {is_buy} | Size: {rounded_size} | Px: {rounded_price} | ReduceOnly: {reduce_only} | TIF: {order_type}")
            
            # Record the API weight for the order
            self.rate_budget.record_request(weight=5)
            
            # P2: Use order_type parameter (IOC for signal-driven, GTC for resting MM/grid)
            order_result = await asyncio.to_thread(
                self.exchange.order,
                coin,
                is_buy,
                rounded_size,
                rounded_price,
                {"limit": {"tif": order_type}},
                reduce_only=reduce_only
            )
            
            db.log_system("INFO", f"Exchange order response: {order_result}")
            
            # 2. Cumulative Limit Interceptor
            if order_result.get("status") == "err":
                err_resp = str(order_result.get("response", ""))
                if "Too many cumulative requests" in err_resp:
                    self._handle_cumulative_limit_error(coin)
            
            if order_result.get("status") == "ok":
                # F3: Entry trades recorded with pnl=None; only exits have pnl values
                trade_pnl = 0.0 if reduce_only else None
                db.record_trade(coin, "BUY" if is_buy else "SELL", rounded_size, rounded_price, pnl=trade_pnl, cloid="L1_TX")
                
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

