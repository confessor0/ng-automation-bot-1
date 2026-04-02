"""
Fyers API Rate Limiter
======================
Centralized rate limiting for all Fyers API calls.
Implements token bucket algorithm with Fyers official limits.

Official Fyers Limits:
- 10 requests/second
- 200 requests/minute  
- 100,000 requests/day
"""
import time
import threading
from collections import deque

class FyersRateLimiter:
    """
    Thread-safe singleton rate limiter using token bucket algorithm.
    Ensures all Fyers API calls stay within rate limits.
    """
    _instance = None
    _lock = threading.Lock()
    
    # Fyers limits with safety margin
    MAX_PER_SECOND = 8  # Official: 10, leave 2 buffer
    MAX_PER_MINUTE = 150  # Official: 200, leave 50 buffer
    MIN_REQUEST_INTERVAL = 0.15  # 150ms minimum between requests
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._request_lock = threading.Lock()
        self._last_request_time = 0
        self._requests_this_second = deque(maxlen=self.MAX_PER_SECOND)
        self._requests_this_minute = deque(maxlen=self.MAX_PER_MINUTE)
        self._total_requests_today = 0
        self._day_start = time.time()
        self._initialized = True
    
    def wait_if_needed(self, caller: str = "unknown"):
        """
        Wait if rate limit would be exceeded.
        Call this BEFORE making any Fyers API request.
        
        Args:
            caller: Name of the calling function for logging
        """
        with self._request_lock:
            now = time.time()
            
            # Reset daily counter if new day
            if now - self._day_start > 86400:  # 24 hours
                self._total_requests_today = 0
                self._day_start = now
            
            # Clean up old timestamps
            cutoff_second = now - 1.0
            cutoff_minute = now - 60.0
            
            while self._requests_this_second and self._requests_this_second[0] < cutoff_second:
                self._requests_this_second.popleft()
            
            while self._requests_this_minute and self._requests_this_minute[0] < cutoff_minute:
                self._requests_this_minute.popleft()
            
            # Check per-second limit
            if len(self._requests_this_second) >= self.MAX_PER_SECOND:
                wait_time = self._requests_this_second[0] + 1.0 - now
                if wait_time > 0:
                    time.sleep(wait_time)
                    now = time.time()
            
            # Check per-minute limit
            if len(self._requests_this_minute) >= self.MAX_PER_MINUTE:
                wait_time = self._requests_this_minute[0] + 60.0 - now
                if wait_time > 0:
                    print(f"⏳ Rate limit: waiting {wait_time:.1f}s (minute limit) [{caller}]")
                    time.sleep(wait_time)
                    now = time.time()
            
            # Enforce minimum interval between requests
            time_since_last = now - self._last_request_time
            if time_since_last < self.MIN_REQUEST_INTERVAL:
                sleep_time = self.MIN_REQUEST_INTERVAL - time_since_last
                time.sleep(sleep_time)
                now = time.time()
            
            # Record this request
            self._requests_this_second.append(now)
            self._requests_this_minute.append(now)
            self._last_request_time = now
            self._total_requests_today += 1
    
    def get_stats(self):
        """Get current rate limiter statistics."""
        return {
            "requests_last_second": len(self._requests_this_second),
            "requests_last_minute": len(self._requests_this_minute),
            "total_today": self._total_requests_today,
            "limit_second": self.MAX_PER_SECOND,
            "limit_minute": self.MAX_PER_MINUTE
        }


# Global instance for easy import
_rate_limiter = None

def get_rate_limiter() -> FyersRateLimiter:
    """Get the singleton rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = FyersRateLimiter()
    return _rate_limiter

def throttle(caller: str = "api"):
    """
    Convenience function to apply rate limiting.
    Call before any Fyers API request.
    
    Usage:
        from src.rate_limiter import throttle
        throttle("get_quotes")
        response = fyers.quotes(data)
    """
    get_rate_limiter().wait_if_needed(caller)
