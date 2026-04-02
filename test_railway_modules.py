#!/usr/bin/env python3
"""
Comprehensive Module Test Suite for NG Automation Bot on Railway
Tests all API endpoints and modules against the deployed Railway instance.

Usage:
    python test_railway_modules.py --url https://YOUR-URL.railway.app
    python test_railway_modules.py --url http://localhost:5000  # For local testing
"""

import argparse
import requests
import json
import time
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)

class RailwayModuleTester:
    def __init__(self, base_url, username="admin", password="password123"):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.results = []
        self.passed = 0
        self.failed = 0
        
    def log_result(self, module, test_name, passed, details=""):
        status = "✅ PASS" if passed else "❌ FAIL"
        color = Fore.GREEN if passed else Fore.RED
        print(f"{color}{status}{Style.RESET_ALL} | {module}: {test_name}")
        if details and not passed:
            print(f"       Details: {details}")
        
        self.results.append({
            "module": module,
            "test": test_name,
            "passed": passed,
            "details": details,
            "timestamp": datetime.now().isoformat()
        })
        
        if passed:
            self.passed += 1
        else:
            self.failed += 1
            
    def test_endpoint(self, method, endpoint, module, test_name, 
                      expected_status=200, json_data=None, expect_key=None):
        """Generic endpoint tester"""
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "GET":
                resp = self.session.get(url, timeout=30)
            elif method == "POST":
                resp = self.session.post(url, json=json_data, timeout=30)
            else:
                self.log_result(module, test_name, False, f"Unknown method: {method}")
                return None
                
            # Check status code
            if resp.status_code != expected_status:
                self.log_result(module, test_name, False, 
                              f"Status {resp.status_code}, expected {expected_status}")
                return None
                
            # Try parse JSON
            try:
                data = resp.json()
            except:
                data = resp.text
                
            # Check expected key if specified
            if expect_key and isinstance(data, dict):
                if expect_key not in data:
                    self.log_result(module, test_name, False, 
                                  f"Missing key: {expect_key}")
                    return data
                    
            self.log_result(module, test_name, True)
            return data
            
        except requests.exceptions.Timeout:
            self.log_result(module, test_name, False, "Request timed out")
            return None
        except requests.exceptions.ConnectionError as e:
            self.log_result(module, test_name, False, f"Connection error: {e}")
            return None
        except Exception as e:
            self.log_result(module, test_name, False, str(e))
            return None
            
    # =============================================
    # TEST CATEGORIES
    # =============================================
    
    def test_authentication(self):
        """Test login/logout and session management"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Authentication Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # Test login
        login_data = {"username": self.username, "password": self.password}
        resp = self.session.post(f"{self.base_url}/login", data=login_data, allow_redirects=False)
        
        if resp.status_code in [302, 200]:
            self.log_result("Authentication", "Login with valid credentials", True)
        else:
            self.log_result("Authentication", "Login with valid credentials", False,
                          f"Status: {resp.status_code}")
            return False
            
        # [FIX] Endpoint doesn't exist in web_server.py snippet, removing check or replacing with profile check
        # self.test_endpoint("GET", "/api/credential-status", ...)
        return True
        
    def test_core_state(self):
        """Test state management module"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Core State Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        data = self.test_endpoint("GET", "/api/state", 
                                 "StateManager", "Get system state",
                                 expect_key="status")
        
        if data:
            # Verify essential state keys
            required_keys = ["status", "logs"]
            for key in required_keys:
                if key in data:
                    self.log_result("StateManager", f"State contains '{key}'", True)
                else:
                    self.log_result("StateManager", f"State contains '{key}'", False)
        
        # Test workflow state
        self.test_endpoint("GET", "/api/workflow/state",
                          "StateManager", "Get workflow state")
                          
    def test_market_data(self):
        """Test market data and refresh functionality"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Market Data Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # [FIX] Use POST for refresh
        data = self.test_endpoint("POST", "/api/refresh",
                                 "MarketDataEngine", "Refresh market data",
                                 expect_key="success")
        
        if data and data.get("success"):
            # Verify market data fields
            if "market_price" in data:
                price = data.get("market_price", 0)
                self.log_result("MarketDataEngine", f"LTP fetched: {price}", price > 0)
            
            if "trend" in data:
                trend = data.get("trend", "")
                # Trend is a dict in new response structure
                if isinstance(trend, dict):
                    trend_val = trend.get("trend", "NEUTRAL")
                else:
                    trend_val = trend
                    
                valid_trends = ["BULLISH", "BEARISH", "NEUTRAL", "WAITING"]
                self.log_result("StrategyEngine", f"Trend: {trend_val}", 
                              trend_val in valid_trends)
                              
            if "atm_strike" in data:
                atm = data.get("atm_strike", 0)
                self.log_result("MarketDataEngine", f"ATM Strike: {atm}", atm > 0)
                
            if "funds" in data:
                funds = data.get("funds", {})
                self.log_result("Broker", "Funds data available", 
                              isinstance(funds, dict))
                              
    def test_user_profile(self):
        """Test user profile module"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: User Profile Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # [FIX] Path is /api/profile
        self.test_endpoint("GET", "/api/profile",
                          "Broker", "Get user profile")
                          
    def test_orders_module(self):
        """Test order management endpoints"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Orders Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # Get current orders - [FIX] Path /api/orders/current
        self.test_endpoint("GET", "/api/orders/current",
                          "OrderExecutor", "Get current orders")
        
        # Get order history - [FIX] Path /api/orders/history
        self.test_endpoint("GET", "/api/orders/history",
                          "OrderExecutor", "Get order history")
                          
        # Get live orders from Fyers
        self.test_endpoint("GET", "/api/live-orders",
                          "Broker", "Get live orders from Fyers")
                          
    def test_positions_module(self):
        """Test positions module"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Positions Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # Get live positions - [FIX] Path /api/live-positions
        self.test_endpoint("GET", "/api/live-positions",
                          "Broker", "Get live positions")
                          
        # Get positions analysis
        self.test_endpoint("GET", "/api/positions/analysis",
                          "PositionsAnalyzer", "Get position analysis")
                          
    def test_strategy_module(self):
        """Test strategy state machine"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Strategy Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # Get strategy state - [FIX] Path /api/strategy/state
        self.test_endpoint("GET", "/api/strategy/state",
                          "StrategyStateEngine", "Get strategy state")
                          
        # Get adjustments status - [FIX] Path /api/adjustment/status
        self.test_endpoint("GET", "/api/adjustment/status",
                          "AdjustmentEngine", "Get adjustment status")
                          
    def test_admin_module(self):
        """Test admin endpoints"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Admin Module")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        # Get Fyers auth URL - [FIX] Path /api/admin/auth_url
        self.test_endpoint("GET", "/api/admin/auth_url",
                          "TokenManager", "Get Fyers auth URL")
                          
        # Get audit logs - [FIX] Path /api/audit/logs
        self.test_endpoint("GET", "/api/audit/logs",
                          "Admin", "Get audit logs")
                          
    def test_dashboard_page(self):
        """Test that dashboard HTML loads"""
        print(f"\n{Fore.CYAN}{'='*50}")
        print("📋 TESTING: Dashboard Page")
        print(f"{'='*50}{Style.RESET_ALL}")
        
        try:
            resp = self.session.get(f"{self.base_url}/", timeout=30)
            if resp.status_code == 200 and "NG Automation" in resp.text:
                self.log_result("WebServer", "Dashboard loads", True)
            else:
                self.log_result("WebServer", "Dashboard loads", False,
                              f"Status: {resp.status_code}")
        except Exception as e:
            self.log_result("WebServer", "Dashboard loads", False, str(e))
            
    # =============================================
    # MAIN TEST RUNNER
    # =============================================
    
    def run_all_tests(self):
        """Execute all test categories"""
        print(f"\n{Fore.YELLOW}{'='*60}")
        print(f"🚀 NG AUTOMATION - RAILWAY MODULE TEST SUITE")
        print(f"   URL: {self.base_url}")
        print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}{Style.RESET_ALL}")
        
        # Run tests in order
        if not self.test_authentication():
            print(f"\n{Fore.RED}⚠️ Authentication failed! Some tests may fail.{Style.RESET_ALL}")
            
        self.test_dashboard_page()
        self.test_core_state()
        self.test_market_data()
        self.test_user_profile()
        self.test_orders_module()
        self.test_positions_module()
        self.test_strategy_module()
        self.test_admin_module()
        
        # Summary
        print(f"\n{Fore.YELLOW}{'='*60}")
        print("📊 TEST SUMMARY")
        print(f"{'='*60}{Style.RESET_ALL}")
        
        total = self.passed + self.failed
        pass_rate = (self.passed / total * 100) if total > 0 else 0
        
        print(f"   Total Tests: {total}")
        print(f"   {Fore.GREEN}Passed: {self.passed}{Style.RESET_ALL}")
        print(f"   {Fore.RED}Failed: {self.failed}{Style.RESET_ALL}")
        print(f"   Pass Rate: {pass_rate:.1f}%")
        
        if self.failed == 0:
            print(f"\n{Fore.GREEN}✅ ALL TESTS PASSED!{Style.RESET_ALL}")
        else:
            print(f"\n{Fore.YELLOW}⚠️ Some tests failed. Review details above.{Style.RESET_ALL}")
            
        # Save results to file
        with open("test_results.json", "w") as f:
            json.dump({
                "url": self.base_url,
                "timestamp": datetime.now().isoformat(),
                "summary": {
                    "total": total,
                    "passed": self.passed,
                    "failed": self.failed,
                    "pass_rate": pass_rate
                },
                "results": self.results
            }, f, indent=2)
        print(f"\n📄 Results saved to test_results.json")
        
        return self.failed == 0


def main():
    parser = argparse.ArgumentParser(description="Test NG Automation modules on Railway")
    parser.add_argument("--url", required=True, help="Railway deployment URL")
    parser.add_argument("--username", default="admin", help="Login username")
    parser.add_argument("--password", default="password123", help="Login password")
    args = parser.parse_args()
    
    tester = RailwayModuleTester(args.url, args.username, args.password)
    success = tester.run_all_tests()
    
    exit(0 if success else 1)


if __name__ == "__main__":
    main()
