
# NG Automation Bot - Master System Blueprint

## Executive Summary
The **NG Automation Bot** is a high-frequency, algorithmic trading system designed for the Natural Gas (NG) Mini futures market on the Fyers platform. It automates a specific **Options-Led Straight Hedge Trend Strategy**, using daily moving averages (MA9) of ATM options to determine trend direction for futures trading. The system prioritizes risk management through a "Canonical Execution Workflow" that treats orders as first-class entities with strict state transitions, manual verification steps, and automated risk scaling.

---

## 1. System Architecture
The application follows a **Modular Engine Architecture** running within a Flask-based Python application.

### Core Engines
1.  **MarketDataEngine**: Features real-time LTP fetching (caching strategy), symbol construction, and historic data retrieval.
2.  **StrategyEngine**: Implements the benchmark strategy logic (Trend Analysis, Signal Generation).
3.  **StrategyStateEngine**: Manages the high-level trading phases (NO_TRADE -> SIGNAL_DETECTED -> IN_POSITION -> OBSERVATION) using a finite state machine.
4.  **OrderIntentEngine**: Generates "Order Intents" (draft orders) from strategy signals, handling validation and user approval logic.
5.  **OrderExecutor**: A sequential execution system that processes validated baskets, ensuring atomic order placement to Fyers.
6.  **AdjustmentEngine**: Monitors open positions for risk rules (e.g., "Sustain < MA9") and triggers scaling actions.
7.  **StateManager**: A singleton centralized store for all application state (Global State Management), persisted via JSON/SQLite.

### Technology Stack
*   **Language**: Python 3.10+
*   **Web Framework**: Flask (Serving UI & API)
*   **Broker API**: Fyers API V3
*   **Frontend**: HTML5/Vanilla JS (Single Page Application dashboard)
*   **Deployment**: Railway.app (Dockerized)
*   **Database**: SQLite (Local/Persistent) + In-Memory State

---

## 2. Core Strategy Logic
**Strategy Name**: Options-Led Straight Hedge Trend Strategy (Benchmark)

### Trend Determination
*   **Input**: Daily Charts of ATM Call (CE) and Put (PE) Options.
*   **Indicator**: 9 Period Moving Average (MA9).
*   **Rules**:
    *   **BULLISH**: ATM **PE** Close < PE MA9 (Puts decaying).
    *   **BEARISH**: ATM **CE** Close < CE MA9 (Calls decaying).
    *   **NEUTRAL**: Both > MA9 (Chop) or Both < MA9 (Rare/Expansion).

### Execution Rules (Canonical Workflow)
1.  **Entry**:
    *   Buy 5 Lots of NG Mini Futures (Direction matching trend).
    *   Buy 5 Lots of OTM Options (Hedge).
    *   Sell 2 Lots of ATM Call + Sell 2 Lots of ATM Put (Iron Fly Protection).
2.  **Risk Scaling (Adjustment)**:
    *   **Trigger**: If the "safe" option (e.g., CE in Bullish) drops and **sustains below MA9 for 6 hours**.
    *   **Action**: "Scale Down" -> Exit 2 Futures. Reduce exposure from 5 to 3 lots.
3.  **Observation Mode**:
    *   Active after scaling.
    *   **Lock**: 24-hour mandatory wait (no re-entry).
    *   **Exit Rule**: If opposing signal worsens (trend reversal confirmed), exit remaining positions.

---

## 3. Directory Structure
```
NG Automation/
├── bot.py                  # Main Entry Point (Trading Loop)
├── Procfile                # Railway Deployment Config
├── requirements.txt        # Python Dependencies
├── templates/
│   └── index.html          # Main Dashboard UI
├── src/
│   ├── adjustment_engine.py    # Risk Scaling Logic
│   ├── auth_server.py          # OAuth2 Server
│   ├── broker.py               # Fyers Connector
│   ├── config.py               # Configuration Constants
│   ├── database.py             # SQLite Persistence
│   ├── execution.py            # Trade Execution Logic
│   ├── instrument_manager.py   # Symbol Resolution
│   ├── market_data.py          # Data Fetching
│   ├── order_executor.py       # Sequential Order Processing
│   ├── order_intent.py         # Intent Lifecycle Management
│   ├── positions_analyzer.py   # MTM & Greek Analysis
│   ├── state_manager.py        # Central State Store
│   ├── strategy.py             # Trend Analysis Logic
│   ├── strategy_state.py       # Strategy State Machine
│   ├── token_manager.py        # Token Lifecycle & Auto-Refresh
│   └── web_server.py           # Flask API Endpoints
└── .env                    # Secrets (Excluded from Repo)
```

---

## 4. Key Data Models (State)

### Global State (`state_manager.py`)
```json
{
  "status": "RUNNING",
  "trend": "BULLISH",
  "market_price": 250.50,
  "atm_strike": 250,
  "strategy_phase": "IN_POSITION",
  "ng_strategy_state": {
    "trend_bias": "BULLISH",
    "signal_source": "NATGASMINI24JAN250PE"
  },
  "order_execution_state": {
    "status": "IDLE",
    "basket_id": "...",
    "orders": [...]
  },
  "adjustment_context": {
    "sustenance_timer": "2.5h / 6.0h",
    "progress_percent": 41
  },
  "observation_context": {
    "is_locked": true,
    "lock_expiry": "14:00 (Tomorrow)"
  }
}
```

### Order Intent Object
```json
{
  "intent_id": "INT-20240120-001",
  "strategy": "Benchmark Hedge",
  "direction": "BULLISH",
  "basket": [
    {"symbol": "NATGASMINIFUT", "qty": 5, "type": "BUY"},
    {"symbol": "NATGASMINI...PE", "qty": 5, "type": "BUY"},
    ...
  ],
  "status": "WAITING_USER_APPROVAL",
  "created_at": "..."
}
```

---

## 5. API Interface (Web Server)

### Market Data
*   `GET /api/refresh`: Triggers on-demand calculation loop (Trend, P&L, Analysis) and returns updated state.
*   `GET /api/state`: Returns raw JSON state.

### Trading Operations
*   `POST /api/orders/force-create`: Manually creates a basket (Entry/Exit).
*   `POST /api/orders/approve`: Approves a pending basket for execution.
*   `POST /api/orders/cancel`: Cancels a pending intent.

### Admin
*   `POST /api/admin/set_mode`: Switch between LIVE / OBSERVER / PANIC modes.
*   `POST /api/authenticate`: Fyers OAuth flow.

---

## 6. Frontend Logic (`index.html`)
*   **Architecture**: Single HTML file with embedded CSS/JS.
*   **Key Components**:
    *   **Status Banner**: Shows Connection, Market Status, Last Refresh.
    *   **Trend Engine**: Displays CE/PE MA9 status and Final Trend badge.
    *   **Recommended Position Panel**: Shows the "Order Intent" (Basket) waiting for approval.
    *   **Execution Console**: Real-time table of executing orders.
    *   **Positions Analyzer**: Live MTM, Greeks, and Simulated P&L.
*   **Logic**:
    *   Polls `/api/state` for background updates.
    *   `manualRefreshAll()` triggers `/api/refresh` to run backend logic.
    *   Syncs "Order Intent" state to show Approve/Reject buttons dynamically.

---

## 7. Application "System Prompt"
*If checking this project into an AI context, use the following description:*

> "You are the NG Automation Bot, a Python-based algorithmic trading system for Natural Gas Mini Futures. You operate on a flask server, using the Fyers API for market data and execution. Your core logic is defined in `src/strategy.py` following the 'Options-Led Straight Hedge' benchmark. You manage state via `StateManager` and execute trades through a strict `OrderIntent` -> `Validation` -> `SequentialExecution` workflow. You prioritize risk management, using an `AdjustmentEngine` to scale down positions if trend strength falters (sustained close below MA9). Your frontend is a dashboard in `templates/index.html` that visualizes this state and solicits user approval for all major actions."

---
