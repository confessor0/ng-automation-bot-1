import requests
import json
from colorama import Fore, Style, init

init()

url = "https://worker-production-18fb.up.railway.app"
print(f"Testing {url}/api/refresh")

try:
    # First login to get session
    s = requests.Session()
    login = s.post(f"{url}/login", data={"username": "admin", "password": "password123"})
    print(f"Login Status: {login.status_code}")
    
    # Refresh
    resp = s.post(f"{url}/api/refresh")
    print(f"Refresh Code: {resp.status_code}")
    
    try:
        data = resp.json()
        print(f"\n{Fore.GREEN}RAW JSON RESPONSE:{Style.RESET_ALL}")
        print(json.dumps(data, indent=2))
        
        if "ltp" in data:
            print(f"\n{Fore.CYAN}Found 'ltp': {data['ltp']} (Type: {type(data['ltp'])}){Style.RESET_ALL}")
        else:
            print(f"\n{Fore.RED}KEY 'ltp' NOT FOUND!{Style.RESET_ALL}")
            
    except Exception as e:
        print(f"JSON Decode Error: {e}")
        print(f"Raw Text: {resp.text}")

except Exception as e:
    print(f"Request Error: {e}")
