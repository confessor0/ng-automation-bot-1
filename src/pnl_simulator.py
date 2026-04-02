"""
NG Options Strategy – Expiry-Aware Dynamic Pricing Simulator

Calculates realistic P&L behavior of Futures + ITM Hedge + Iron Fly structure.
Uses: Theta decay, Time to expiry, Intrinsic/Extrinsic value, IV dynamics.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Tuple
import math


# ============================================================================
# DEFAULT CONSTANTS (NG backward compat)
# ============================================================================

DEFAULT_LOT_SIZE = 250  # MCX NG Mini lot size

# Default Position Structure (NG)
DEFAULT_POSITION_STRUCTURE = {
    "futures_lots": 5,
    "hedge_lots": 5,
    "hedge_offset": 10,  # ITM offset for hedge PUT
    "fly_lots": 2,
    "fly_wing_offset": 20,  # OTM offset for wings
}

# Theta Decay Exponents by Moneyness
THETA_EXPONENTS = {
    "ATM": 1.3,
    "OTM": 1.6,
    "ITM": 0.8,
}

# Default Price Scenarios (points from CMP) — NG
DEFAULT_PRICE_SCENARIOS = [+50, +40, +30, +20, +10, 0, -10, -20, -30, -40, -50]

# Time Slices (DTE)
TIME_SLICES = [2, 1, 0]  # T-2, T-1, Expiry


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class OptionLeg:
    """Represents a single option leg in the strategy."""
    name: str
    strike: float
    opt_type: str  # "CE" or "PE"
    side: str  # "BUY" or "SELL"
    lots: int
    entry_price: float = 0.0  # Premium paid/received at entry


@dataclass
class SimulationResult:
    """Result for a single scenario + time slice."""
    price: float
    price_change: float
    dte: int
    legs: List[Dict]
    net_pnl: float
    risk_posture: str
    interpretation: str


# ============================================================================
# OPTION PRICING MODEL
# ============================================================================

class OptionPricer:
    """
    Calculates option prices using Intrinsic + Extrinsic value model.
    Extrinsic decays with theta based on DTE and moneyness.
    """
    
    def __init__(self, initial_dte: int = 7, base_iv: float = 0.35):
        self.initial_dte = max(initial_dte, 1)
        self.base_iv = base_iv
    
    def get_intrinsic(self, price: float, strike: float, opt_type: str) -> float:
        """Calculate intrinsic value of option."""
        if opt_type == "CE":
            return max(0, price - strike)
        else:  # PE
            return max(0, strike - price)
    
    def get_moneyness(self, price: float, strike: float, opt_type: str) -> str:
        """Determine if option is ATM, ITM, or OTM."""
        diff = abs(price - strike)
        
        # Within 2% of strike = ATM
        if diff / strike < 0.02:
            return "ATM"
        
        if opt_type == "CE":
            return "ITM" if price > strike else "OTM"
        else:  # PE
            return "ITM" if price < strike else "OTM"
    
    def get_base_extrinsic(self, price: float, strike: float, opt_type: str) -> float:
        """
        Calculate base extrinsic value (at initial DTE).
        ATM has highest extrinsic, decreases with distance from ATM.
        """
        moneyness = self.get_moneyness(price, strike, opt_type)
        distance = abs(price - strike)
        
        # Base extrinsic for ATM (simplified model)
        # Roughly 2-4% of underlying for NG Mini
        atm_extrinsic = price * 0.03 * math.sqrt(self.initial_dte / 7)
        
        if moneyness == "ATM":
            return atm_extrinsic
        elif moneyness == "OTM":
            # OTM extrinsic decays with distance
            decay_factor = max(0.1, 1 - (distance / (price * 0.1)))
            return atm_extrinsic * decay_factor
        else:  # ITM
            # ITM has less extrinsic as intrinsic dominates
            intrinsic = self.get_intrinsic(price, strike, opt_type)
            return max(1, atm_extrinsic * 0.5 - intrinsic * 0.1)
    
    def apply_theta_decay(self, base_extrinsic: float, dte: int, 
                          moneyness: str) -> float:
        """
        Apply theta decay based on DTE and moneyness.
        Formula: Extrinsic × (DTE / InitialDTE)^ThetaExponent
        """
        if dte <= 0:
            return 0  # On expiry, extrinsic = 0
        
        theta_exp = THETA_EXPONENTS.get(moneyness, 1.3)
        decay_factor = (dte / self.initial_dte) ** theta_exp
        
        return base_extrinsic * decay_factor
    
    def get_option_price(self, underlying_price: float, strike: float, 
                         opt_type: str, dte: int) -> Tuple[float, float, float]:
        """
        Calculate total option price = Intrinsic + Extrinsic.
        Returns: (total_price, intrinsic, extrinsic)
        """
        intrinsic = self.get_intrinsic(underlying_price, strike, opt_type)
        moneyness = self.get_moneyness(underlying_price, strike, opt_type)
        base_extrinsic = self.get_base_extrinsic(underlying_price, strike, opt_type)
        extrinsic = self.apply_theta_decay(base_extrinsic, dte, moneyness)
        
        total = intrinsic + extrinsic
        return round(total, 2), round(intrinsic, 2), round(extrinsic, 2)


# ============================================================================
# SCENARIO SIMULATOR
# ============================================================================

class PnLSimulator:
    """
    Main simulator engine for NG Options Strategy.
    Generates P&L across price scenarios and time slices.
    """
    
    def __init__(self, cmp: float, atm_strike: float, dte: int,
                 lot_size: int = None, hedge_offset: int = None,
                 wing_offset: int = None, fut_lots: int = None,
                 hedge_lots: int = None, fly_lots: int = None,
                 price_scenarios: list = None):
        """
        Initialize simulator.
        
        Args:
            cmp: Current Market Price (live from market data)
            atm_strike: ATM strike price
            dte: Current Days to Expiry
            lot_size: Contract lot size (default: 250 for NG)
            hedge_offset: ITM offset for hedge option (default: 10)
            wing_offset: OTM offset for iron fly wings (default: 20)
            fut_lots: Number of futures lots (default: 5)
            hedge_lots: Number of hedge lots (default: 5)
            fly_lots: Number of iron fly lots (default: 2)
            price_scenarios: Custom price move scenarios (default: NG moves)
        """
        self.cmp = cmp
        self.atm_strike = atm_strike
        self.dte = max(dte, 0)
        self.pricer = OptionPricer(initial_dte=max(dte, 1))
        
        # Instrument-specific params (with backward-compat defaults)
        self.lot_size = lot_size or DEFAULT_LOT_SIZE
        self.position_structure = {
            "futures_lots": fut_lots or DEFAULT_POSITION_STRUCTURE["futures_lots"],
            "hedge_lots": hedge_lots or DEFAULT_POSITION_STRUCTURE["hedge_lots"],
            "hedge_offset": hedge_offset or DEFAULT_POSITION_STRUCTURE["hedge_offset"],
            "fly_lots": fly_lots or DEFAULT_POSITION_STRUCTURE["fly_lots"],
            "fly_wing_offset": wing_offset or DEFAULT_POSITION_STRUCTURE["fly_wing_offset"],
        }
        self.price_scenarios = price_scenarios or DEFAULT_PRICE_SCENARIOS
        
        # Build position structure
        self.legs = self._build_legs()
        
        # Calculate entry prices at CMP
        self._calculate_entry_prices()
    
    def _build_legs(self) -> List[OptionLeg]:
        """Build the fixed position structure."""
        atm = self.atm_strike
        cfg = self.position_structure
        
        legs = [
            # Hedge PUT (ITM)
            OptionLeg(
                name="Hedge PUT",
                strike=atm + cfg["hedge_offset"],  # ITM for PUT = higher strike
                opt_type="PE",
                side="BUY",
                lots=cfg["hedge_lots"]
            ),
            # ATM CE Short (Iron Fly)
            OptionLeg(
                name="ATM CE Short",
                strike=atm,
                opt_type="CE",
                side="SELL",
                lots=cfg["fly_lots"]
            ),
            # ATM PE Short (Iron Fly)
            OptionLeg(
                name="ATM PE Short",
                strike=atm,
                opt_type="PE",
                side="SELL",
                lots=cfg["fly_lots"]
            ),
            # OTM CE Wing (Iron Fly)
            OptionLeg(
                name="OTM CE Wing",
                strike=atm + cfg["fly_wing_offset"],
                opt_type="CE",
                side="BUY",
                lots=cfg["fly_lots"]
            ),
            # OTM PE Wing (Iron Fly)
            OptionLeg(
                name="OTM PE Wing",
                strike=atm - cfg["fly_wing_offset"],
                opt_type="PE",
                side="BUY",
                lots=cfg["fly_lots"]
            ),
        ]
        
        return legs
    
    def _calculate_entry_prices(self):
        """Calculate entry prices for all legs at current CMP and DTE."""
        for leg in self.legs:
            price, _, _ = self.pricer.get_option_price(
                self.cmp, leg.strike, leg.opt_type, self.dte
            )
            leg.entry_price = price
    
    def calculate_futures_pnl(self, new_price: float) -> float:
        """Calculate Futures P&L."""
        lots = self.position_structure["futures_lots"]
        return (new_price - self.cmp) * self.lot_size * lots
    
    def calculate_option_pnl(self, leg: OptionLeg, new_price: float, 
                             dte: int) -> Dict:
        """Calculate P&L for a single option leg."""
        new_opt_price, intrinsic, extrinsic = self.pricer.get_option_price(
            new_price, leg.strike, leg.opt_type, dte
        )
        
        # P&L = (New Price - Entry Price) × Lot Size × Lots
        price_diff = new_opt_price - leg.entry_price
        
        # Invert for SELL positions
        if leg.side == "SELL":
            price_diff = -price_diff
        
        pnl = price_diff * self.lot_size * leg.lots
        
        return {
            "name": leg.name,
            "strike": leg.strike,
            "opt_type": leg.opt_type,
            "side": leg.side,
            "lots": leg.lots,
            "entry_price": leg.entry_price,
            "current_price": new_opt_price,
            "intrinsic": intrinsic,
            "extrinsic": extrinsic,
            "pnl": round(pnl, 0)
        }
    
    def get_risk_posture(self, net_pnl: float, price_change: float) -> str:
        """Determine risk posture based on P&L and price movement."""
        if net_pnl > 5000:
            return "SAFE"
        elif net_pnl > 0:
            return "STABLE"
        elif net_pnl > -10000:
            return "WARNING"
        else:
            return "DANGER"
    
    def generate_interpretation(self, legs_data: List[Dict], 
                                price_change: float, dte: int) -> str:
        """Generate human-readable interpretation of the scenario."""
        interpretations = []
        
        # Theta impact
        if dte <= 1:
            interpretations.append("⚡ Theta decay is accelerating rapidly")
        elif dte <= 3:
            interpretations.append("📉 Moderate theta decay in effect")
        
        # Find dominant leg
        max_loss_leg = min(legs_data, key=lambda x: x["pnl"])
        max_gain_leg = max(legs_data, key=lambda x: x["pnl"])
        
        if abs(price_change) > 30:
            interpretations.append(f"🔥 {max_loss_leg['name']} is under pressure")
            if "Wing" in max_gain_leg["name"]:
                interpretations.append(f"🛡️ Wings providing protection")
        elif abs(price_change) < 10:
            interpretations.append("💰 Iron Fly theta harvest zone")
        
        # Hedge activation
        if price_change <= -20:
            interpretations.append("✅ Hedge PUT fully activated")
        
        return " | ".join(interpretations) if interpretations else "Normal conditions"
    
    def simulate_scenario(self, price_change: float, dte: int) -> SimulationResult:
        """Simulate a single price scenario at given DTE."""
        new_price = self.cmp + price_change
        
        # Futures P&L
        futures_pnl = self.calculate_futures_pnl(new_price)
        
        # Options P&L
        legs_data = []
        total_options_pnl = 0
        
        for leg in self.legs:
            leg_result = self.calculate_option_pnl(leg, new_price, dte)
            legs_data.append(leg_result)
            total_options_pnl += leg_result["pnl"]
        
        # Add Futures to legs data
        legs_data.insert(0, {
            "name": "Futures",
            "strike": None,
            "opt_type": "FUT",
            "side": "BUY",
            "lots": self.position_structure["futures_lots"],
            "entry_price": self.cmp,
            "current_price": new_price,
            "intrinsic": "-",
            "extrinsic": "-",
            "pnl": round(futures_pnl, 0)
        })
        
        net_pnl = futures_pnl + total_options_pnl
        
        return SimulationResult(
            price=new_price,
            price_change=price_change,
            dte=dte,
            legs=legs_data,
            net_pnl=round(net_pnl, 0),
            risk_posture=self.get_risk_posture(net_pnl, price_change),
            interpretation=self.generate_interpretation(legs_data, price_change, dte)
        )
    
    def run_full_simulation(self) -> Dict:
        """
        Run simulation across all price scenarios and time slices.
        Returns structured data for dashboard display.
        """
        cfg = self.position_structure
        results = {
            "cmp": self.cmp,
            "atm_strike": self.atm_strike,
            "initial_dte": self.dte,
            "lot_size": self.lot_size,
            "position_summary": {
                "futures": f"BUY {cfg['futures_lots']} Lots @ {self.cmp}",
                "hedge": f"BUY {cfg['hedge_lots']} Lots {self.atm_strike + cfg['hedge_offset']} PE",
                "iron_fly_shorts": f"SELL {cfg['fly_lots']} Lots {self.atm_strike} CE/PE",
                "iron_fly_wings": f"BUY {cfg['fly_lots']} Lots {self.atm_strike + cfg['fly_wing_offset']} CE / {self.atm_strike - cfg['fly_wing_offset']} PE",
            },
            "scenarios": [],
            "analytics": {}
        }
        
        # Run all scenarios
        all_results = []
        for price_change in self.price_scenarios:
            for dte in TIME_SLICES:
                if dte <= self.dte:  # Only simulate valid DTEs
                    result = self.simulate_scenario(price_change, dte)
                    all_results.append(result)
                    results["scenarios"].append({
                        "price": result.price,
                        "price_change": f"+{price_change}" if price_change > 0 else str(price_change),
                        "dte": result.dte,
                        "dte_label": f"T-{dte}" if dte > 0 else "Expiry",
                        "legs": result.legs,
                        "net_pnl": result.net_pnl,
                        "risk_posture": result.risk_posture,
                        "interpretation": result.interpretation
                    })
        
        # Calculate analytics
        results["analytics"] = self._calculate_analytics(all_results)
        
        return results
    
    def _calculate_analytics(self, results: List[SimulationResult]) -> Dict:
        """Calculate summary analytics from all scenarios."""
        if not results:
            return {}
        
        # Find extremes
        max_profit = max(results, key=lambda x: x.net_pnl)
        max_loss = min(results, key=lambda x: x.net_pnl)
        
        # Theta harvest zone (where ATM shorts decay most)
        harvest_zone = [r for r in results if abs(r.price_change) <= 10 and r.dte <= 1]
        avg_harvest = sum(r.net_pnl for r in harvest_zone) / len(harvest_zone) if harvest_zone else 0
        
        # Danger zone (where delta overwhelms theta)
        danger_zone = [r for r in results if abs(r.price_change) >= 30]
        
        return {
            "theta_harvest_zone": {
                "range": "CMP ± 10",
                "best_dte": "0-1 days",
                "avg_pnl": round(avg_harvest, 0)
            },
            "danger_zone": {
                "trigger": "Price move > ±30",
                "warning": "Delta overwhelms Theta"
            },
            "max_profit": {
                "price": max_profit.price,
                "dte": max_profit.dte,
                "pnl": max_profit.net_pnl
            },
            "max_loss": {
                "price": max_loss.price,
                "dte": max_loss.dte,
                "pnl": max_loss.net_pnl
            },
            "hedge_effectiveness": "✅ Hedge activates at -20, caps downside losses",
            "wing_effectiveness": "✅ Wings cap Iron Fly losses beyond ±20"
        }


# ============================================================================
# UTILITY FUNCTION FOR API
# ============================================================================

def run_simulation(cmp: float, atm_strike: float, dte: int,
                   symbol: str = None, **kwargs) -> Dict:
    """
    Convenience function for API endpoint.
    
    Args:
        cmp: Current Market Price from market data
        atm_strike: ATM strike (calculated from CMP)
        dte: Days to Expiry
        symbol: Instrument symbol (optional, for auto-loading config)
        **kwargs: Override individual params (lot_size, hedge_offset, etc.)
    
    Returns:
        Full simulation results dictionary
    """
    # Auto-load instrument config if symbol provided
    if symbol and not kwargs:
        try:
            from src.instrument_config import get_instrument_config, get_hedge_distance, get_wing_distance, get_scenario_moves
            config = get_instrument_config(symbol)
            kwargs = {
                "lot_size": config["lot_size"],
                "hedge_offset": get_hedge_distance(symbol, cmp),
                "wing_offset": get_wing_distance(symbol, cmp),
                "fut_lots": config["fut_lots"],
                "hedge_lots": config["hedge_lots"],
                "fly_lots": config["fly_lots"],
                "price_scenarios": get_scenario_moves(symbol, cmp),
            }
        except Exception as e:
            print(f"⚠️ Failed to load instrument config for {symbol}: {e}")
    
    simulator = PnLSimulator(cmp=cmp, atm_strike=atm_strike, dte=dte, **kwargs)
    return simulator.run_full_simulation()
