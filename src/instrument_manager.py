"""
Multi-Instrument Manager
Handles multiple instruments with independent strategies
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

class InstrumentConfig:
    """Configuration for a single instrument"""
    def __init__(self, symbol: str, instrument_type: str, lot_size: int, strategy_type: str = "MA9"):
        self.symbol = symbol
        self.instrument_type = instrument_type  # NATGASMINI, CRUDEOILM, GOLDM, etc.
        self.lot_size = lot_size
        self.strategy_type = strategy_type
        self.active = True
        self.added_at = datetime.now().isoformat()
        
    def to_dict(self):
        return {
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "lot_size": self.lot_size,
            "strategy_type": self.strategy_type,
            "active": self.active,
            "added_at": self.added_at
        }
    
    @classmethod
    def from_dict(cls, data):
        config = cls(
            symbol=data["symbol"],
            instrument_type=data["instrument_type"],
            lot_size=data["lot_size"],
            strategy_type=data.get("strategy_type", "MA9")
        )
        config.active = data.get("active", True)
        config.added_at = data.get("added_at", datetime.now().isoformat())
        return config


class InstrumentManager:
    """Manages multiple trading instruments"""
    
    _instance = None
    
    def __init__(self):
        self.instruments: Dict[str, InstrumentConfig] = {}
        self.watchlist: List[Dict] = []
        self.config_file = "instruments_config.json"
        self.load_config()
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def add_instrument(self, symbol: str, instrument_type: str, lot_size: int, strategy_type: str = "MA9") -> bool:
        """Add a new instrument to track"""
        if symbol in self.instruments:
            return False
        
        config = InstrumentConfig(symbol, instrument_type, lot_size, strategy_type)
        self.instruments[symbol] = config
        self.save_config()
        return True
    
    def remove_instrument(self, symbol: str) -> bool:
        """Remove an instrument"""
        if symbol in self.instruments:
            del self.instruments[symbol]
            self.save_config()
            return True
        return False
    
    def get_instrument(self, symbol: str) -> Optional[InstrumentConfig]:
        """Get instrument configuration"""
        return self.instruments.get(symbol)
    
    def get_all_instruments(self) -> List[Dict]:
        """Get all instruments as list of dicts"""
        return [config.to_dict() for config in self.instruments.values()]
    
    def get_active_instruments(self) -> List[Dict]:
        """Get only active instruments"""
        return [config.to_dict() for config in self.instruments.values() if config.active]
    
    def toggle_instrument(self, symbol: str, active: bool) -> bool:
        """Activate or deactivate an instrument"""
        if symbol in self.instruments:
            self.instruments[symbol].active = active
            self.save_config()
            return True
        return False
    
    def add_to_watchlist(self, symbol: str, symbol_type: str) -> bool:
        """Add symbol to watchlist"""
        # Check if already in watchlist
        if any(w["symbol"] == symbol for w in self.watchlist):
            return False
        
        self.watchlist.append({
            "symbol": symbol,
            "type": symbol_type,
            "added_at": datetime.now().isoformat()
        })
        self.save_config()
        return True
    
    def remove_from_watchlist(self, symbol: str) -> bool:
        """Remove symbol from watchlist"""
        original_len = len(self.watchlist)
        self.watchlist = [w for w in self.watchlist if w["symbol"] != symbol]
        if len(self.watchlist) < original_len:
            self.save_config()
            return True
        return False
    
    def get_watchlist(self) -> List[Dict]:
        """Get all watchlist items"""
        return self.watchlist
    
    def save_config(self):
        """Save configuration to file"""
        config = {
            "instruments": {k: v.to_dict() for k, v in self.instruments.items()},
            "watchlist": self.watchlist,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
    
    def load_config(self):
        """Load configuration from file"""
        if not os.path.exists(self.config_file):
            # Initialize with all supported instruments
            from src.instrument_config import INSTRUMENT_CALIBRATION
            for symbol, config in INSTRUMENT_CALIBRATION.items():
                self.add_instrument(symbol, symbol, config["lot_size"], "MA9")
            return
        
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            # Load instruments
            for symbol, data in config.get("instruments", {}).items():
                self.instruments[symbol] = InstrumentConfig.from_dict(data)
            
            # Load watchlist
            self.watchlist = config.get("watchlist", [])
            
        except Exception as e:
            print(f"Error loading config: {e}")
            # Initialize with default
            self.add_instrument("NATGASMINI", "NATGASMINI", 1250, "MA9")
