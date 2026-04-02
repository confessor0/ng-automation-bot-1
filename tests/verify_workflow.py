import sys
import os
import time

# Add src to path
sys.path.append(os.getcwd())

from src.state_manager import StateManager
from src.order_executor import OrderExecutor

class MockBroker:
    def get_positions(self):
        return []

def test_basket_ready_flow():
    state_mgr = StateManager.get_instance()
    # Reset state
    state_mgr.update("workflow_state", "NO_TRADE")
    
    broker = MockBroker()
    executor = OrderExecutor(broker)
    
    print("Initial Workflow State:", state_mgr.get_workflow_state()["state"])
    
    # Simulate Main Logic identifying Trend
    trend = "BEARISH"
    atm_strike = 300
    expiry = "26JAN"
    
    # 1. Create Basket
    basket_id = executor.create_basket(trend, atm_strike, expiry, basket_type="ENTRY")
    print(f"Basket Created: {basket_id}")
    
    # 2. Transition State (as done in main.py)
    state_mgr.transition_workflow_state("SIGNAL_CONFIRMED")
    state_mgr.transition_workflow_state("BASKET_READY", basket_type="ENTRY")
    
    # 3. Verify
    exec_state = state_mgr.get_state().get("order_execution_state", {})
    workflow = state_mgr.get_workflow_state()
    
    print("\n--- VERIFICATION ---")
    print(f"Workflow State: {workflow['state']} (Expected: BASKET_READY)")
    print(f"Basket ID in State: {exec_state.get('basket_id')} (Expected: {basket_id})")
    print(f"Basket Status: {exec_state.get('status')} (Expected: PENDING)")
    
    if workflow['state'] == "BASKET_READY" and exec_state.get('status') == "PENDING" and exec_state.get('basket_id') == basket_id:
        print("\n✅ TEST PASSED: Execution Page will show Basket Ready.")
    else:
        print("\n❌ TEST FAILED.")

if __name__ == "__main__":
    test_basket_ready_flow()
