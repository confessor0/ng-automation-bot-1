
import sys
import os
import time
import random
from datetime import datetime
from colorama import Fore, Style

# Add current directory to path
sys.path.append(os.getcwd())

from src.state_manager import StateManager
from src.strategy import StrategyEngine
from src.market_data import MarketDataEngine

# --- Mock Broker for Stability Testing ---
# We mock the broker to allow running this test without a real Fyers session / Market hours.
# This focuses on logic stability, memory usage, and error handling.
class StabilityMockBroker:
    def __init__(self):
        self.fyers = "Connected" # Dummy truthy object
        self.request_count = 0
        self.error_count = 0

    def get_quotes(self, symbols):
        self.request_count += 1
        
        # 5% chance of random network failure
        if random.random() < 0.05:
            self.error_count += 1
            # print(f"{Fore.YELLOW}   [Sim] Network Glitch / Timeout...{Style.RESET_ALL}")
            return {} 
            
        # Return valid but fluctuating data
        base_price = 250.0 + (random.random() * 10 - 5)
        return {s: base_price for s in symbols}

    def quotes(self, data):
        self.request_count += 1
        # Similar logic for direct API calls
        if random.random() < 0.05:
            self.error_count += 1
            return {"s": "error", "code": 504, "message": "Gateway Timeout"}
            
        return {"s": "ok", "d": [{"n": "TEST", "v": {"lp": 250.0 + random.random()}}]}

    def history(self, data):
        # Return a standard candle set
        return {
            "s": "ok",
            "candles": [
                [time.time() - 86400*2, 240, 255, 238, 250, 1000],
                [time.time() - 86400, 250, 260, 245, 252, 1200]
            ]
        }

def run_stability_test(duration_minutes=60):
    print(f"{Fore.CYAN}🚀 Starting Stability Test (Duration: {duration_minutes}m)...{Style.RESET_ALL}")
    print(f"   Covering: Market Data, Trend Engine, Dynamic Legs, System Health")
    
    # 1. Setup
    broker = StabilityMockBroker()
    try:
        market_data = MarketDataEngine(broker)
    except Exception as e:
        import traceback
        print(f"{Fore.RED}❌ MarketDataEngine init error: {e}\"")
        traceback.print_exc()
        raise
    try:
        strategy = StrategyEngine(broker, market_data)
    except Exception as e:
        import traceback
        print(f"{Fore.RED}❌ StrategyEngine init error: {e}\"")
        traceback.print_exc()
        raise
    state_mgr = StateManager.get_instance()
    
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)
    iteration = 0
    failures = 0
    
    try:
        while time.time() < end_time:
            iteration += 1
            iter_start = time.time()
            
            # --- A. Refresh Logic Simulation ---
            # 1. Fetch LTP (MarketDataEngine)
            try:
                ltp = market_data._get_ltp("MCX:NATGASMINI24FEBFUT")
            except Exception as e:
                print(f"{Fore.RED}❌ LTP Fetch Crash: {e}{Style.RESET_ALL}")
                failures += 1
                
            # 2. Analyze Trend (StrategyEngine)
            try:
                trend, atm, details = strategy.analyze_trend("MCX:NATGASMINI24FEBFUT", "24FEB")
                
                # Verify Caching worked (Check manually or infer speed)
                # If iteration > 1, history fetch should be instant (Mock broker history isn't called)
            except Exception as e:
                import traceback
                print(f"{Fore.RED}❌ Strategy Engine Crash: {e}")
                traceback.print_exc()
                print(f"{Style.RESET_ALL}")
                failures += 1

            # 3. Simulate Leg Generation (Web Server Logic copy)
            try:
                # Minimal simulation of the logic in web_server.py
                if trend in ["BULLISH", "BEARISH"]:
                     # Simulate batch fetch for legs
                     legs = ["OPT_CE", "OPT_PE"]
                     quotes = market_data._get_batch_ltp(legs)
                     if not quotes and ltp > 0:
                         # This shouldn't crash
                         pass
            except Exception as e:
                print(f"{Fore.RED}❌ Leg Gen Crash: {e}{Style.RESET_ALL}")
                failures += 1

            # --- Reporting ---
            elapsed = time.time() - start_time
            if iteration % 10 == 0:
                print(f"   ⏱️  Iter {iteration} | Elapsed: {elapsed:.1f}s | Failures: {failures} | Broker Reqs: {broker.request_count} (Errs: {broker.error_count})")
                
            # Sleep to simulate real-world usage (e.g. 1 refresh every 2s)
            # For test speed, we might go faster, but let's stick to 1s
            time.sleep(0.1) 
            
            # Stop early for this demo run (Limiting to ~30s or user intervention)
            if duration_minutes == "DEMO" and iteration >= 50:
                print(f"{Fore.GREEN}✅ Demo Limit Reached (50 iterations).{Style.RESET_ALL}")
                break

    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}⚠️ Test Interrupted by User.{Style.RESET_ALL}")
        
    print(f"\n{Fore.CYAN}🏁 Stability Test Complete.{Style.RESET_ALL}")
    print(f"   Total Iterations: {iteration}")
    print(f"   Total Crashes: {failures}")
    print(f"   Broker Network Errors Simulated: {broker.error_count}")
    
    if failures == 0:
        print(f"{Fore.GREEN}✅ PASSED: System is Stable.{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}❌ FAILED: Crashes Detected.{Style.RESET_ALL}")

if __name__ == "__main__":
    # check args
    args = sys.argv
    dur = "DEMO"
    if len(args) > 1:
        try:
            dur = int(args[1])
        except:
            pass
            
    run_stability_test(dur)
