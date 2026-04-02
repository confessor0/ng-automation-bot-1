import time
from colorama import Fore, Style
from datetime import datetime, timedelta
import pandas as pd
from functools import lru_cache
from src.utils import get_next_expiry_symbol_suffix, get_atm_strike, construct_symbol
from src.rate_limiter import throttle

class MarketDataEngine:
    def __init__(self, broker, symbol_base="NATGASMINI"):
        self.broker = broker
        self.symbol_base = symbol_base
        
        # Cache for reducing API calls
        self._price_cache = {}
        self._cache_ttl = 30  # seconds (on-demand mode: longer cache to protect API quota)
        
        self.expiry_suffix = get_next_expiry_symbol_suffix(symbol=self.symbol_base)
        self.fut_symbol = construct_symbol(self.symbol_base, "FUT", self.expiry_suffix)
        self.atm_strike = 0
        self.ce_symbol = None
        self.pe_symbol = None
        
    def fetch_market_depth(self):
        """
        Main loop to fetch and display data.
        """
        # 1. Fetch Futures Price first to determine ATM
        fut_lp = self._get_ltp(self.fut_symbol)
        
        if fut_lp == 0:
            return None
        
        # 2. Determine ATM
        self.atm_strike = get_atm_strike(fut_lp, self.symbol_base)
        
        # 3. Construct Option Symbols
        self.ce_symbol = construct_symbol(self.symbol_base, "OPT", self.expiry_suffix, self.atm_strike, "CE")
        self.pe_symbol = construct_symbol(self.symbol_base, "OPT", self.expiry_suffix, self.atm_strike, "PE")
        
        # 4. Fetch Option Prices
        symbols = [self.fut_symbol, self.ce_symbol, self.pe_symbol]
        quotes = self._get_batch_ltp(symbols)
        
        # 5. Output
        self._print_dashboard(quotes)
        
        return quotes

    def _get_ltp(self, symbol):
        """
        Get Last Traded Price for a single symbol.
        Falls back to previous close if market is closed.
        """
        if not self.broker.fyers:
            # Simulation Data for Demo
            print(f"{Fore.BLUE}[SIM] Fetching LTP for {symbol}: 245.50{Style.RESET_ALL}")
            return 245.50
            
        # [NEW] Check Cache
        now = time.time()
        if symbol in self._price_cache:
            val, ts = self._price_cache[symbol]
            if now - ts < self._cache_ttl:
                # print(f"✨ Cache Hit for {symbol}: {val}")
                return val

        # Retry logic for LTP
        retries = 5  # increased from 3
        backoff = 2  # increased start from 1
        
        for attempt in range(retries):
            try:
                throttle("get_ltp")  # Rate limiting
                data = {"symbols": symbol}
                resp = self.broker.fyers.quotes(data=data)
                
                # Check for 429 Rate Limit
                if resp.get("code") == 429 or "limit reached" in str(resp).lower():
                    print(f"{Fore.YELLOW}⚠️ Market Data Rate Limit for {symbol}. Retrying in {backoff}s... (Attempt {attempt+1}/{retries}){Style.RESET_ALL}")
                    time.sleep(backoff)
                    backoff *= 2 # 2, 4, 8, 16, 32...
                    if backoff > 10: backoff = 10 # Cap at 10s
                    continue
                
                if resp.get("s") == "ok":
                    ltp = resp["d"][0]["v"].get("lp", 0.0)
                    
                    # If LTP is 0 (market closed), try to get previous close
                    if ltp == 0:
                        ltp = resp["d"][0]["v"].get("prev_close_price", 0.0)
                        if ltp > 0:
                            print(f"{Fore.YELLOW}ℹ️  Market Closed. Using Previous Close: {ltp}{Style.RESET_ALL}")
                    
                    # Update Cache
                    if ltp > 0:
                        self._price_cache[symbol] = (ltp, now)
                    
                    return ltp
                else:
                    msg = resp.get("message", "Unknown")
                    if "expire" in msg.lower() or "invalid" in msg.lower():
                         print(f"{Fore.RED}❌ Token Invalid during price fetch.{Style.RESET_ALL}")
                         return 0.0
                    
            except Exception as e:
                print(f"{Fore.RED}Error fetching {symbol}: {e}{Style.RESET_ALL}")
                time.sleep(1)
        
        print(f"{Fore.RED}❌ Failed to fetch {symbol} after {retries} retries.{Style.RESET_ALL}")
        return 0.0

    def _get_batch_ltp(self, symbols):
        """
        Get LTP for multiple symbols.
        Optimized with partial caching: checks cache first, fetches only missing.
        """
        results = {}
        missing = []
        now = time.time()
        
        # 1. Check Cache
        for s in symbols:
            if s in self._price_cache:
                val, ts = self._price_cache[s]
                if now - ts < self._cache_ttl:
                    results[s] = val
                else:
                    missing.append(s)
            else:
                missing.append(s)
        
        if not missing:
            return results

        if not self.broker.fyers:
            return {s: 0.0 for s in symbols}

        # 2. Fetch Missing
        retries = 5
        backoff = 2
        
        fetched_prices = {}
        
        for attempt in range(retries):
            try:
                throttle("batch_ltp")
                sym_str = ",".join(missing)
                data = {"symbols": sym_str}
                resp = self.broker.fyers.quotes(data=data)
                
                if resp.get("code") == 429 or "limit reached" in str(resp).lower():
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                if resp.get("s") == "ok":
                    for item in resp.get("d", []):
                        val = item["v"].get("lp", 0.0)
                        sym = item["n"]
                        fetched_prices[sym] = val
                        # Update Cache
                        if val > 0:
                            self._price_cache[sym] = (val, now)
                    break # Success
                else:
                    if "expire" in str(resp).lower(): break
                    
            except Exception as e:
                print(f"Error fetching batch quotes: {e}")
                time.sleep(1)
        
        # Merge fetched into results
        results.update(fetched_prices)
        
        # Fill zeros for failed ones
        for m in missing:
            if m not in results:
                results[m] = 0.0
                
        return results

    def _print_dashboard(self, quotes):
        print("\n" + "="*60)
        print(f"{Fore.CYAN}📊 MARKET DATA SNAPSHOT (Expiry: {self.expiry_suffix}){Style.RESET_ALL}")
        print("="*60)
        print(f"{'SYMBOL':<30} | {'LTP':<10} | {'TYPE':<10}")
        print("-" * 60)
        
        for sym, price in quotes.items():
            type_ = "FUT" if "FUT" in sym else "OPT"
            # Highlight ATM
            if type_ == "OPT":
                if str(self.atm_strike) in sym:
                    type_ = "ATM " + sym[-2:]
            
            print(f"{sym:<30} | {price:<10.2f} | {type_:<10}")
        
        print("-" * 60)
        print(f"🎯 DETECTED ATM STRIKE: {self.atm_strike}")
