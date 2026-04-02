"""
Positions Analyzer Engine
Calculates position health metrics for adjustment decisions.
Without this, Adjustment Engine cannot function.
"""

import json
import os
from datetime import datetime
from src.state_manager import StateManager


class PositionsAnalyzer:
    """
    Analyzes current positions to determine:
    - Net delta
    - Net premium
    - Spot distance from strikes
    - Hedge coverage %
    
    Stores snapshot every candle close for historical analysis.
    """
    
    SNAPSHOTS_FILE = "position_snapshots.json"
    
    def __init__(self, broker):
        self.broker = broker
        self.state_mgr = StateManager.get_instance()
    
    def analyze(self, symbol_base: str = "NATGASMINI") -> dict:
        """
        Performs full position analysis.
        
        Returns comprehensive health metrics.
        """
        positions = self._get_positions(symbol_base)
        
        if not positions:
            return {
                "has_positions": False,
                "analysis": None,
                "timestamp": datetime.now().isoformat()
            }
        
        # Calculate metrics
        analysis = {
            "timestamp": datetime.now().isoformat(),
            "symbol_base": symbol_base,
            "has_positions": True,
            "position_count": len(positions),
            "positions": positions,
            
            # P&L Metrics
            "total_mtm": self._calculate_total_mtm(positions),
            "unrealized_pnl": self._calculate_unrealized_pnl(positions),
            
            # Greeks (simplified)
            "net_delta": self._calculate_net_delta(positions),
            "delta_status": self._get_delta_status(positions),
            
            # Premium
            "net_premium_collected": self._calculate_premium(positions),
            
            # Position Structure
            "futures_qty": self._get_futures_qty(positions),
            "options_breakdown": self._get_options_breakdown(positions),
            
            # Hedge Health
            "hedge_coverage_pct": self._calculate_hedge_coverage(positions),
            "hedge_status": self._get_hedge_status(positions),
            
            # Risk Alerts
            "risk_alerts": self._check_risk_alerts(positions)
        }
        
        # Store in state for UI
        self.state_mgr.update("positions_analysis", analysis)
        
        return analysis
    
    def _get_positions(self, symbol_base: str) -> list:
        """Fetch positions from broker filtered by symbol"""
        try:
            all_positions = self.broker.get_positions()
            return [p for p in all_positions if symbol_base in p.get('symbol', '')]
        except Exception as e:
            print(f"Error fetching positions: {e}")
            return []
    
    def _calculate_total_mtm(self, positions: list) -> float:
        """Calculate total MTM P&L"""
        return sum(float(p.get('pl', 0)) for p in positions)
    
    def _calculate_unrealized_pnl(self, positions: list) -> float:
        """Calculate unrealized P&L"""
        return sum(float(p.get('unrealizedProfit', 0)) for p in positions)
    
    def _calculate_net_delta(self, positions: list) -> float:
        """
        Calculate net delta exposure.
        Simplified: 
        - Futures delta = qty (1 per lot)
        - CE delta ≈ +0.5 for ATM, less for OTM
        - PE delta ≈ -0.5 for ATM, less for OTM
        """
        net_delta = 0.0
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            qty = int(pos.get('netQty', 0))
            
            if 'FUT' in symbol:
                # Futures: delta = 1 per qty
                net_delta += qty
            elif 'CE' in symbol:
                # Call options: positive delta
                # Approximate based on position (buy adds delta, sell subtracts)
                delta_per_lot = 0.4  # Simplified approximation
                net_delta += qty * delta_per_lot
            elif 'PE' in symbol:
                # Put options: negative delta
                delta_per_lot = -0.4
                net_delta += qty * delta_per_lot
        
        return round(net_delta, 2)
    
    def _get_delta_status(self, positions: list) -> str:
        """Determine delta status for strategy"""
        net_delta = self._calculate_net_delta(positions)
        
        if abs(net_delta) <= 0.25:
            return "NEUTRAL"
        elif abs(net_delta) <= 0.5:
            return "SLIGHT_BIAS"
        elif abs(net_delta) <= 1.0:
            return "MODERATE_BIAS"
        else:
            return "HIGH_BIAS"
    
    def _calculate_premium(self, positions: list) -> float:
        """Calculate net premium from option positions"""
        premium = 0.0
        for pos in positions:
            symbol = pos.get('symbol', '')
            if 'CE' in symbol or 'PE' in symbol:
                # Sold options = positive premium
                # Bought options = negative premium
                qty = int(pos.get('netQty', 0))
                avg_price = float(pos.get('avgPrice', 0))
                lot_size = 250  # NATGASMINI lot size
                
                if qty < 0:  # Sold
                    premium += abs(qty) * avg_price * lot_size
                else:  # Bought
                    premium -= qty * avg_price * lot_size
        
        return round(premium, 2)
    
    def _get_futures_qty(self, positions: list) -> int:
        """Get total futures quantity"""
        for pos in positions:
            if 'FUT' in pos.get('symbol', ''):
                return int(pos.get('netQty', 0))
        return 0
    
    def _get_options_breakdown(self, positions: list) -> dict:
        """Break down option positions"""
        breakdown = {
            "long_calls": 0,
            "short_calls": 0,
            "long_puts": 0,
            "short_puts": 0
        }
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            qty = int(pos.get('netQty', 0))
            
            if 'CE' in symbol:
                if qty > 0:
                    breakdown["long_calls"] += qty
                else:
                    breakdown["short_calls"] += abs(qty)
            elif 'PE' in symbol:
                if qty > 0:
                    breakdown["long_puts"] += qty
                else:
                    breakdown["short_puts"] += abs(qty)
        
        return breakdown
    
    def _calculate_hedge_coverage(self, positions: list) -> float:
        """
        Calculate hedge coverage percentage.
        Good hedge = long options cover short options / futures risk.
        """
        breakdown = self._get_options_breakdown(positions)
        futures_qty = abs(self._get_futures_qty(positions))
        
        total_long = breakdown["long_calls"] + breakdown["long_puts"]
        total_short = breakdown["short_calls"] + breakdown["short_puts"]
        
        # Need at least 1 long for every 2 shorts, plus hedge for futures
        required_longs = (total_short / 2) + (futures_qty / 2)
        
        if required_longs == 0:
            return 100.0
        
        coverage = (total_long / required_longs) * 100
        return min(round(coverage, 1), 100.0)
    
    def _get_hedge_status(self, positions: list) -> str:
        """Determine hedge health status"""
        coverage = self._calculate_hedge_coverage(positions)
        
        if coverage >= 90:
            return "FULLY_HEDGED"
        elif coverage >= 70:
            return "ADEQUATELY_HEDGED"
        elif coverage >= 50:
            return "UNDER_HEDGED"
        else:
            return "CRITICALLY_EXPOSED"
    
    def _check_risk_alerts(self, positions: list) -> list:
        """Check for risk conditions requiring attention"""
        alerts = []
        
        mtm = self._calculate_total_mtm(positions)
        net_delta = self._calculate_net_delta(positions)
        hedge_coverage = self._calculate_hedge_coverage(positions)
        
        # MTM Alert
        if mtm < -5000:
            alerts.append({
                "type": "MTM_CRITICAL",
                "message": f"MTM Loss exceeds ₹5,000: ₹{mtm:,.0f}",
                "severity": "CRITICAL"
            })
        elif mtm < -2000:
            alerts.append({
                "type": "MTM_WARNING",
                "message": f"MTM Loss: ₹{mtm:,.0f}",
                "severity": "WARNING"
            })
        
        # Delta Alert
        if abs(net_delta) > 1.0:
            alerts.append({
                "type": "DELTA_HIGH",
                "message": f"Net delta too high: {net_delta}",
                "severity": "WARNING"
            })
        
        # Hedge Alert
        if hedge_coverage < 50:
            alerts.append({
                "type": "HEDGE_CRITICAL",
                "message": f"Hedge coverage critically low: {hedge_coverage}%",
                "severity": "CRITICAL"
            })
        elif hedge_coverage < 80:
            alerts.append({
                "type": "HEDGE_WARNING",
                "message": f"Hedge coverage below target: {hedge_coverage}%",
                "severity": "WARNING"
            })
        
        return alerts
    
    def save_snapshot(self) -> dict:
        """Save current analysis as a point-in-time snapshot"""
        analysis = self.state_mgr.get_state().get("positions_analysis", {})
        
        if not analysis:
            return {"success": False, "message": "No analysis to snapshot"}
        
        try:
            snapshots = []
            if os.path.exists(self.SNAPSHOTS_FILE):
                with open(self.SNAPSHOTS_FILE, 'r') as f:
                    snapshots = json.load(f)
            
            # Add candle timestamp
            analysis["candle_time"] = datetime.now().isoformat()
            snapshots.append(analysis)
            
            # Keep last 100 snapshots
            if len(snapshots) > 100:
                snapshots = snapshots[-100:]
            
            with open(self.SNAPSHOTS_FILE, 'w') as f:
                json.dump(snapshots, f, indent=2)
            
            return {"success": True, "message": "Snapshot saved"}
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    @staticmethod
    def get_snapshots(limit: int = 50) -> list:
        """Retrieve historical snapshots"""
        try:
            if not os.path.exists(PositionsAnalyzer.SNAPSHOTS_FILE):
                return []
            with open(PositionsAnalyzer.SNAPSHOTS_FILE, 'r') as f:
                snapshots = json.load(f)
            return list(reversed(snapshots[-limit:]))
        except:
            return []
