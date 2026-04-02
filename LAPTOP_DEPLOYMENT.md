# NG Mini Trading Bot - Laptop Deployment Guide

## 🎯 Why Laptop is a Great Choice

### ✅ Advantages
- **Zero hosting cost** - Save ₹299/month
- **Full control** - Direct access to everything
- **Easy debugging** - Immediate access to logs
- **No deployment complexity** - Already working!
- **Fast updates** - Edit and restart instantly

### ⚠️ Considerations
- **Must run during market hours** (9:15 AM - 11:30 PM)
- **Stable internet required**
- **Power backup recommended**
- **Sleep mode must be disabled**

## 🚀 Production Setup for Laptop

### Step 1: Prevent Sleep/Hibernation

#### Windows Power Settings
```
1. Open Settings → System → Power & Sleep
2. Set "Screen" to: Never
3. Set "Sleep" to: Never
4. Click "Additional power settings"
5. Choose "High Performance" plan
6. Edit plan settings:
   - Turn off display: Never
   - Put computer to sleep: Never
```

#### Advanced Settings
```
Control Panel → Power Options → Change plan settings → Change advanced power settings:
- Hard disk → Turn off hard disk after: Never
- Sleep → Sleep after: Never
- Sleep → Hibernate after: Never
- USB settings → USB selective suspend: Disabled
```

### Step 2: Auto-Start Bot on Windows Boot

Create a startup script and schedule it to run automatically.

#### Option A: Task Scheduler (Recommended)
1. Open Task Scheduler (Win + R → `taskschd.msc`)
2. Create Basic Task:
   - Name: "NG Trading Bot"
   - Trigger: "At log on"
   - Action: "Start a program"
   - Program: `C:\Users\ahmed\Desktop\NG Automation\start_bot.bat`
   - Finish

#### Option B: Startup Folder
1. Press Win + R
2. Type: `shell:startup`
3. Create shortcut to `start_bot.bat` in this folder

### Step 3: Network Stability

#### Reserve IP Address
```
1. Open Router settings (usually 192.168.1.1)
2. Find DHCP settings
3. Reserve IP for your laptop's MAC address
4. This prevents IP changes
```

#### Backup Internet (Optional)
- Keep mobile hotspot ready
- Configure automatic failover in bot

### Step 4: Monitoring & Alerts

#### Setup Windows Notifications
- Bot will show desktop notifications for important events
- Keep sound enabled for alerts

#### Remote Access (Optional)
- Enable Windows Remote Desktop
- Access bot from phone/another PC

## 📋 Daily Workflow

### Morning Routine (Before 9:15 AM)
```batch
1. Wake up laptop
2. Check internet connection
3. Run: start_bot.bat
4. Open dashboard: http://localhost:5050
5. Verify Fyers connection
6. Check DRY_RUN setting
```

### During Market Hours
```
- Keep laptop plugged in
- Monitor dashboard periodically
- Check for adjustment alerts
- Approve trades in Execution tab
```

### Evening Routine (After 11:30 PM)
```batch
1. Review day's trades
2. Check logs for errors
3. Backup important data
4. Stop bot: stop_bot.bat (optional)
```

## 🔧 Troubleshooting

### Bot Stops Unexpectedly
```batch
# Check if Python crashed
tasklist | findstr python

# Restart bot
start_bot.bat

# Check error logs
type logs\error.log
```

### Internet Disconnection
```
- Bot will retry Fyers connection automatically
- Check logs for reconnection status
- Verify positions after reconnection
```

### Laptop Overheating
```
- Clean laptop vents
- Use cooling pad
- Monitor CPU usage (should be <20%)
- Close unnecessary applications
```

## 💾 Backup Strategy

### Daily Automatic Backup
```batch
# Create backup.bat
@echo off
set BACKUP_DIR=C:\NG_Backups
set DATE=%date:~-4,4%%date:~-7,2%%date:~-10,2%
mkdir %BACKUP_DIR%\%DATE%
xcopy "C:\Users\ahmed\Desktop\NG Automation\*.log" %BACKUP_DIR%\%DATE%\ /Y
xcopy "C:\Users\ahmed\Desktop\NG Automation\.env" %BACKUP_DIR%\%DATE%\ /Y
echo Backup completed: %BACKUP_DIR%\%DATE%
```

### Schedule Daily Backup
```
Task Scheduler → Create Task
- Trigger: Daily at 11:45 PM
- Action: Run backup.bat
```

## 🌐 Access from Other Devices

### Access Dashboard from Phone
```
1. Find laptop's local IP:
   ipconfig | findstr IPv4

2. On phone (same WiFi):
   http://192.168.1.XXX:5050

3. Login with credentials
```

### Port Forwarding (Access from Outside)
```
⚠️ Security Risk - Not Recommended
If needed:
1. Router → Port Forwarding
2. Forward external port 8080 → laptop IP:5050
3. Access via: http://YOUR_PUBLIC_IP:8080
4. Use strong password!
```

## 🔒 Security Best Practices

### 1. Firewall Rules
```
Windows Defender Firewall → Advanced Settings
- Inbound Rules → New Rule
- Port: 5050
- Allow: Private networks only
```

### 2. Strong Dashboard Password
```python
# In src/web_server.py
ADMIN_PASSWORD = "YourStrongPassword123!"
```

### 3. Encrypt .env File
```batch
# Use Windows EFS encryption
Right-click .env → Properties → Advanced → Encrypt contents
```

## 📊 Performance Optimization

### Close Unnecessary Apps
```
- Chrome/Edge (if not needed)
- OneDrive sync
- Windows Update (schedule for night)
- Antivirus real-time scan (exclude bot folder)
```

### Dedicated User Account (Optional)
```
1. Create new Windows user: "Trader"
2. Auto-login this user
3. Only run bot + browser
4. Minimal startup programs
```

## 🎯 Comparison: Laptop vs VPS

| Feature | Laptop | VPS |
|---------|--------|-----|
| **Cost** | ₹0/month | ₹299/month |
| **Setup** | ✅ Already done | ⚠️ Needs deployment |
| **Control** | ✅ Full access | ⚠️ Remote only |
| **Reliability** | ⚠️ Depends on power | ✅ 99.9% uptime |
| **Internet** | ⚠️ Home connection | ✅ Data center |
| **Maintenance** | ✅ Easy | ⚠️ SSH required |

## 💡 Hybrid Approach (Best of Both)

### Development: Laptop
- Test strategies
- Debug issues
- Quick iterations

### Production: VPS (when stable)
- 24/7 automated trading
- No manual intervention
- Professional setup

## ✅ Laptop Deployment Checklist

- [ ] Power settings configured (no sleep)
- [ ] Auto-start script created
- [ ] Task Scheduler configured
- [ ] Internet connection stable
- [ ] Backup script scheduled
- [ ] Remote access tested (optional)
- [ ] Dashboard accessible
- [ ] Fyers credentials verified
- [ ] DRY_RUN tested
- [ ] Emergency stop procedure known

## 🚨 Emergency Procedures

### Stop Bot Immediately
```batch
# Method 1: Use stop script
stop_bot.bat

# Method 2: Task Manager
Ctrl + Shift + Esc → Find python.exe → End Task

# Method 3: Command line
taskkill /F /IM python.exe
```

### Exit All Positions (Manual)
```
1. Login to Fyers web/app
2. Go to Positions
3. Exit all NG Mini positions
4. Verify exits confirmed
```

## 📞 Support Resources

### Bot Issues
- Check logs: `logs\bot.log`
- Review error log: `logs\error.log`
- Restart: `start_bot.bat`

### Fyers Issues
- Fyers Support: 1800-266-0909
- Web: https://fyers.in/support

### System Issues
- Check internet: `ping google.com`
- Check disk space: `dir C:\`
- Check processes: Task Manager

## 🎯 Recommended: Start with Laptop

**My advice:**
1. **Run on laptop for 1-2 weeks**
   - Test all features
   - Verify strategy works
   - Build confidence

2. **Then move to VPS** (optional)
   - Once everything is stable
   - For truly hands-off trading
   - Better reliability

**Your laptop is perfect for getting started! 🚀**
