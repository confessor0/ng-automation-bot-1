import os
import sys

def verify_env():
    print("="*50)
    print("🔍 RENDER ENVIRONMENT DIAGNOSTICS")
    print("="*50)
    
    env_vars = [
        "FYERS_APP_ID", 
        "FYERS_SECRET_ID",
        "FYERS_ACCESS_TOKEN",
        "FYERS_REDIRECT_URI",
        "PORT"
    ]
    
    missing = []
    found = 0
    
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            # Obscure the value for safety
            obscured = val[:3] + "***" + val[-3:] if len(val) > 6 else "***"
            print(f"✅ {var}: {obscured}")
            found += 1
        else:
            print(f"❌ {var}: NOT FOUND")
            missing.append(var)
            
    print("="*50)
    if missing:
        print(f"⚠️  MISSING {len(missing)} VARIABLES")
        print(f"Please add these to your Render Dashboard Environment Settings:")
        for m in missing:
            print(f"  - {m}")
    else:
        print("🎉 ALL CORE VARIABLES FOUND!")
        
    print("="*50)
    print(f"Current Directory: {os.getcwd()}")
    print(f"Filesystem Writable (test): ", end="")
    try:
        with open("test_write.tmp", "w") as f:
            f.write("test")
        os.remove("test_write.tmp")
        print("YES")
    except Exception as e:
        print(f"NO ({e})")
    print("="*50)

if __name__ == "__main__":
    verify_env()
