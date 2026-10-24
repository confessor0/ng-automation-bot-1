import os
import time
from fyers_apiv3 import fyersModel
from colorama import Fore, Style
from src.config import FYERS_APP_ID, FYERS_ACCESS_TOKEN
from src.rate_limiter import throttle

class FyersConnector:
    def __init__(self):
        self.client_id = FYERS_APP_ID # Use the App ID for connection
        self.access_token = os.getenv("FYERS_ACCESS_TOKEN") # Fetch fresh from env
        self.fyers = None
        self.profile = None

    def connect(self):
        """
        Initializes the FyersModel instance.
        """
        if not self.access_token:
            print(f"{Fore.YELLOW}⚠️  No Access Token found. Please run 'get_token.py'.{Style.RESET_ALL}")
            return False

        try:
            self.fyers = fyersModel.FyersModel(
                client_id=self.client_id, 
                token=self.access_token,
                is_async=False, 
                log_path=""
            )
            # Verify token by fetching profile
            self.profile = self.fyers.get_profile()
            
            if self.profile.get("s") == "ok":
                name = self.profile.get("data", {}).get("name", "Unknown")
                
                print(f"{Fore.GREEN}✅ FYERS CONNECTED: {name} (ID: {self.client_id}){Style.RESET_ALL}")
                return True
            else:
                msg = self.profile.get("message", "Unknown Error")
                if "Token is invalid" in msg or "expire" in msg.lower():
                    print(f"{Fore.RED}❌ FYERS TOKEN EXPIRED/INVALID.{Style.RESET_ALL}")
                    print(f"{Fore.YELLOW}💡 Run 'python get_token.py' to generate a new one.{Style.RESET_ALL}")
                return False
        except Exception as e:
            print(f"{Fore.RED}❌ Connection Failed: {e}{Style.RESET_ALL}")
            return False
            
    def get_funds(self):
        """
        Fetches detailed fund limits.
        Returns a dict: { capital, used_margin, available_balance }
        """
        if not self.fyers:
            # Simulation Data for UI Testing
            return {"capital": 500000.0, "used_margin": 125000.0, "available_balance": 375000.0}
            
        try:
            throttle("get_funds")  # Rate limiting
            funds_resp = self.fyers.funds()
            
            if funds_resp.get("s") == "ok":
                fund_limits = funds_resp.get("fund_limit", [])
                
                capital = 0.0
                used = 0.0
                
                for f in fund_limits:
                    title = f.get("title", "")
                    
                    if title == "Total Balance" or title == "total_balance":
                        equity_amt = f.get("equityAmount", 0.0)
                        commodity_amt = f.get("commodityAmount", 0.0)
                        capital = equity_amt if equity_amt > 0 else commodity_amt
                    elif title == "Utilized Amount" or title == "utilized_amount":
                        equity_used = f.get("equityAmount", 0.0)
                        commodity_used = f.get("commodityAmount", 0.0)
                        used = equity_used if equity_used > 0 else commodity_used
                
                # If still 0, try to get from top-level fields
                if capital == 0:
                    capital = funds_resp.get("total_balance", 0.0)
                        
                available = capital - used
                
                
                return {
                    "capital": capital, 
                    "used_margin": used, 
                    "available_balance": available
                }
            else:
                return {"capital": 0.0, "used_margin": 0.0, "available_balance": 0.0}
        except Exception as e:
            print(f"{Fore.RED}Error in get_funds(): {e}{Style.RESET_ALL}")
            return {"capital": 0.0, "used_margin": 0.0, "available_balance": 0.0}

    def check_symbol_exists(self, symbol):
        """
        Checks if the symbol is valid by fetching a quote depth.
        """
        if not self.fyers: 
            return False
            
        try:
            throttle("check_symbol")  # Rate limiting
            data = {"symbols": symbol}
            response = self.fyers.quotes(data=data)
            if response.get("s") == "ok" and symbol in response.get("d", [])[0].get("n"):
               return True
            return False 
        except:
            return False

    def place_order(self, symbol, side, qty, product_type="NRML", order_type="MARKET"):
        """
        Places an order on Fyers.
        Side: 1 (BUY), -1 (SELL)
        Returns: {"s": "ok", "id": order_id} or {"s": "error", "message": "reason"}
        """
        if not self.fyers:
            print(f"{Fore.RED}❌ Cannot Place Order: Broker Not Connected.{Style.RESET_ALL}")
            return {"s": "error", "message": "Broker Not Connected"}

        try:
            data = {
                "symbol": symbol,
                "qty": qty,
                "type": 2 if order_type == "MARKET" else 1, # 1=Limit, 2=Market
                "side": 1 if side == "BUY" else -1,
                "productType": product_type,
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }
            
            throttle("place_order")  # Rate limiting
            resp = self.fyers.place_order(data=data)
            
            if resp.get("s") == "ok":
                order_id = resp.get("id")
                print(f"{Fore.GREEN}✅ ORDER PLACED: {side} {qty}x {symbol} [ID: {order_id}]{Style.RESET_ALL}")
                return {"s": "ok", "id": order_id}
            else:
                error_msg = resp.get('message', 'Unknown error')
                print(f"{Fore.RED}❌ ORDER FAILED: {error_msg}{Style.RESET_ALL}")
                return {"s": "error", "message": error_msg}
                
                
        except Exception as e:
            print(f"{Fore.RED}❌ Order Exception: {e}{Style.RESET_ALL}")
            return {"s": "error", "message": str(e)}
    
    def get_order_status(self, order_id):
        """
        Fetches the current status of a placed order.
        Returns: {
            "status": "PENDING" | "FILLED" | "REJECTED" | "CANCELLED",
            "tradedPrice": float,
            "qty": int
        }
        """
        if not self.fyers:
            return {"status": "FILLED", "tradedPrice": 10.50, "qty": 1}  # Simulation
        
        try:
            # Fyers orderbook API
            resp = self.fyers.orderbook()
            
            if resp.get("s") == "ok" and "orderBook" in resp:
                for order in resp["orderBook"]:
                    if order.get("id") == order_id:
                        status_map = {
                            "1": "PENDING",
                            "2": "FILLED",
                            "3": "REJECTED",
                            "4": "CANCELLED",
                            "5": "PARTIALLY_FILLED",
                            "6": "TRANSIT"
                        }
                        
                        raw_status = str(order.get("status", "1"))
                        status = status_map.get(raw_status, "PENDING")
                        
                        return {
                            "status": status,
                            "tradedPrice": order.get("tradedPrice", 0.0),
                            "qty": order.get("qty", 0)
                        }
            
            # Order not found or API error
            return {"status": "PENDING", "tradedPrice": 0.0, "qty": 0}
            
        except Exception as e:
            print(f"{Fore.RED}❌ Order Status Exception: {e}{Style.RESET_ALL}")
            return {"status": "PENDING", "tradedPrice": 0.0, "qty": 0}

    def get_all_orders(self):
        """
        Fetches all orders from Fyers orderbook.
        Returns list of orders with standardized format for UI.
        """
        from src.rate_limiter import throttle
        
        if not self.fyers:
            # Simulation data
            return []
        
        try:
            throttle("get_orders")
            resp = self.fyers.orderbook()
            
            if resp.get("s") == "ok" and "orderBook" in resp:
                orders = []
                status_map = {
                    1: "PENDING", 2: "FILLED", 3: "REJECTED", 
                    4: "CANCELLED", 5: "PARTIAL", 6: "TRANSIT"
                }
                side_map = {1: "BUY", -1: "SELL"}
                
                for o in resp.get("orderBook", []):
                    orders.append({
                        "id": o.get("id", ""),
                        "symbol": o.get("symbol", "").replace("MCX:", ""),
                        "side": side_map.get(o.get("side", 1), "BUY"),
                        "qty": o.get("qty", 0),
                        "price": o.get("limitPrice", 0) or o.get("tradedPrice", 0),
                        "traded_price": o.get("tradedPrice", 0),
                        "status": status_map.get(o.get("status", 1), "PENDING"),
                        "order_type": "MARKET" if o.get("type") == 2 else "LIMIT",
                        "product": o.get("productType", "NRML"),
                        "time": o.get("orderDateTime", ""),
                        "message": o.get("message", "")
                    })
                return orders
            else:
                return []
                
        except Exception as e:
            print(f"{Fore.RED}Error fetching orders: {e}{Style.RESET_ALL}")
            return []

    def get_positions(self):
        """
        Fetches current open positions (Net).
        """
        if not self.fyers:
            return []
            
        try:
            # [NEW] Retry Logic for Positions
            retries = 3
            backoff = 1
            
            for attempt in range(retries):
                resp = self.fyers.positions()
                
                # Check for Rate Limit or success
                if resp.get("s") == "ok":
                    return resp.get("netPositions", [])
                
                msg = resp.get("message", "").lower()
                if resp.get("code") == 429 or "limit reached" in msg:
                    print(f"{Fore.YELLOW}⚠️ Positions Rate Limit. Retrying in {backoff}s...{Style.RESET_ALL}")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    print(f"Error fetching positions: {resp.get('message')}")
                    return []
            
            return []
        except Exception as e:
            print(f"Position fetch error: {e}")
            return []

    def close_position(self, symbol, qty):
        """
        Closes a position by placing an opposite order.
        Assumption: Net Qty > 0 means Long (Sell to Close), Net Qty < 0 means Short (Buy to Close).
        """
        if not self.fyers:
            return False
            
        # Determine current side
        positions = self.get_positions()
        target_pos = next((p for p in positions if p.get('symbol') == symbol), None)
        
        if not target_pos:
            print(f"{Fore.YELLOW}⚠️  Close failed: Position for {symbol} not found.{Style.RESET_ALL}")
            return False
            
        net_qty = int(target_pos.get('netQty', 0))
        if net_qty == 0:
            print(f"{Fore.YELLOW}ℹ️  Position {symbol} is already closed.{Style.RESET_ALL}")
            return True
            
        # If NetQty > 0 (Long), we need to SELL. Side = -1
        # If NetQty < 0 (Short), we need to BUY. Side = 1
        side = -1 if net_qty > 0 else 1
        side_str = "SELL" if side == -1 else "BUY"
        
        # Qty to close (ensure positive)
        # If arg qty is provided, use it, else close all.
        # Logic: We usually want to close specific amount.
        qty_to_close = qty if qty > 0 else abs(net_qty)
        
        return self.place_order(symbol, side_str, qty_to_close)
    def get_symbol_info(self, symbol):
        """
        Fetches live info for a symbol to assist in auto-population.
        """
        if not self.fyers:
            return None
        try:
            data = {"symbols": symbol}
            resp = self.fyers.quotes(data=data)
            if resp.get("s") == "ok" and resp.get("d"):
                return resp["d"][0]["v"]
            return None
        except:
            return None

    def get_quotes(self, symbols_list):
        """
        Fetches quotes for multiple symbols.
        Returns a dict: {symbol: ltp}
        """
        if not self.fyers or not symbols_list:
            return {}
            
        try:
            # Join symbols with comma (max 50 per call usually)
            sym_str = ",".join(symbols_list)
            data = {"symbols": sym_str}
            
            # [NEW] Rate Limit Handling with Retries
            retries = 3
            backoff = 2
            
            for attempt in range(retries):
                response = self.fyers.quotes(data=data)
                
                # Check for 429 Rate Limit
                if response.get("code") == 429 or "limit reached" in str(response).lower():
                    print(f"{Fore.YELLOW}⚠️ Rate Limit Reached. Sleeping {backoff}s...{Style.RESET_ALL}")
                    time.sleep(backoff)
                    backoff *= 2
                    continue # Retry
                
                # Success
                if response.get("s") == "ok":
                    result = {}
                    for item in response.get("d", []):
                        sym = item.get("n")
                        lp = item.get("v", {}).get("lp")
                        if sym and lp:
                             result[sym] = lp
                    return result
                
                # Token Expiry Check
                msg = response.get("message", "")
                if "Token" in msg or "expire" in msg.lower() or "Invalid" in msg:
                    self.connect()
                    time.sleep(1)
                    # Loop will retry
                    
                break # If not 429 or Token error, return whatever we got
                
            return {}
        except Exception as e:
            # Reconnect logic if needed
            msg = str(e)
            if "client" in msg.lower() or "connection" in msg.lower():
                print(f"{Fore.RED}⚠️ Connection Error in Quotes. Reconnecting...{Style.RESET_ALL}")
                self.connect()
            return {}
