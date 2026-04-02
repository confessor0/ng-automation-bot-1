# NG Mini Trading Bot - Hostinger VPS Deployment Guide

## 🎯 Quick Start (5 Steps)

### Step 1: Purchase Hostinger VPS
1. Go to [Hostinger VPS](https://www.hostinger.in/vps-hosting)
2. Choose **KVM 1** plan (₹299/month)
3. Select **India** datacenter
4. Choose **Ubuntu 22.04** OS
5. Complete purchase

### Step 2: Access Your VPS
```bash
# You'll receive these via email:
# IP Address: xxx.xxx.xxx.xxx
# Username: root
# Password: xxxxxxxxx

# Connect via SSH (Windows PowerShell or PuTTY)
ssh root@YOUR_VPS_IP
```

### Step 3: Upload Setup Script
```bash
# On your Windows machine (PowerShell)
scp "C:\Users\ahmed\Desktop\NG Automation\hostinger_setup.sh" root@YOUR_VPS_IP:/root/

# On VPS
chmod +x /root/hostinger_setup.sh
bash /root/hostinger_setup.sh
```

### Step 4: Upload Your Bot Code
```bash
# Option A: Using SCP (from Windows)
scp -r "C:\Users\ahmed\Desktop\NG Automation\*" root@YOUR_VPS_IP:/var/www/ng-automation/

# Option B: Using Git (on VPS)
cd /var/www/ng-automation
git clone https://github.com/yourusername/ng-automation.git .
```

### Step 5: Configure and Start
```bash
# On VPS
cd /var/www/ng-automation

# Edit credentials
nano .env
# Add your Fyers credentials, then save (Ctrl+X, Y, Enter)

# Install dependencies
source venv/bin/activate
pip install -r requirements.txt

# Start the bot
./start.sh
```

## 🌐 Access Your Dashboard
```
http://YOUR_VPS_IP
```

## 📊 Management Commands

### Start/Stop/Restart
```bash
cd /var/www/ng-automation
./start.sh      # Start the bot
./stop.sh       # Stop the bot
./restart.sh    # Restart the bot
```

### Monitor Logs
```bash
./logs.sh       # Live logs (Ctrl+C to exit)
./status.sh     # Current status + recent logs
```

### Update Bot
```bash
./update.sh     # Pull latest code and restart
```

## 🔒 Security Setup (Recommended)

### 1. Change Root Password
```bash
passwd
```

### 2. Create Non-Root User
```bash
adduser trader
usermod -aG sudo trader
su - trader
```

### 3. Setup SSH Key Authentication
```bash
# On your Windows machine (PowerShell)
ssh-keygen -t rsa -b 4096

# Copy public key to VPS
scp C:\Users\ahmed\.ssh\id_rsa.pub root@YOUR_VPS_IP:/tmp/

# On VPS
mkdir -p ~/.ssh
cat /tmp/id_rsa.pub >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

### 4. Disable Password Authentication (After SSH key works)
```bash
sudo nano /etc/ssh/sshd_config
# Change: PasswordAuthentication no
sudo systemctl restart sshd
```

## 🌍 Setup Custom Domain (Optional)

### 1. Point Domain to VPS
In your domain registrar (e.g., Hostinger Domains):
```
A Record: @ -> YOUR_VPS_IP
A Record: www -> YOUR_VPS_IP
```

### 2. Install SSL Certificate
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

### 3. Update Nginx Config
```bash
sudo nano /etc/nginx/sites-available/ng-bot
# Change: server_name yourdomain.com www.yourdomain.com;
sudo nginx -t && sudo systemctl reload nginx
```

## 📈 Performance Monitoring

### Check System Resources
```bash
# CPU and Memory
htop

# Disk usage
df -h

# Network usage
iftop
```

### Monitor Bot Performance
```bash
# Real-time logs
./logs.sh

# Check if bot is running
./status.sh

# View error logs
sudo tail -f /var/log/ng-bot-error.log
```

## 🔧 Troubleshooting

### Bot Not Starting
```bash
# Check logs
./logs.sh

# Check Python errors
sudo journalctl -u ng-bot -n 50

# Verify .env file
cat .env

# Test bot manually
cd /var/www/ng-automation
source venv/bin/activate
python bot.py
```

### Dashboard Not Accessible
```bash
# Check Nginx status
sudo systemctl status nginx

# Check firewall
sudo ufw status

# Test local connection
curl http://localhost:5050
```

### High CPU/Memory Usage
```bash
# Check resource usage
top

# Restart bot
./restart.sh

# Check for memory leaks
ps aux | grep python
```

## 💾 Backup Strategy

### Create Backup Script
```bash
cat > /var/www/ng-automation/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/root/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR
tar -czf $BACKUP_DIR/ng-bot-$DATE.tar.gz /var/www/ng-automation
echo "Backup created: $BACKUP_DIR/ng-bot-$DATE.tar.gz"
EOF

chmod +x /var/www/ng-automation/backup.sh
```

### Schedule Daily Backups
```bash
crontab -e
# Add: 0 2 * * * /var/www/ng-automation/backup.sh
```

## 📞 Hostinger Support
- **Panel**: https://hpanel.hostinger.com
- **Support**: 24/7 Live Chat
- **Docs**: https://support.hostinger.com

## 🎯 Production Checklist

- [ ] VPS purchased and accessible
- [ ] Setup script executed
- [ ] Bot code uploaded
- [ ] .env configured with real credentials
- [ ] Bot started successfully
- [ ] Dashboard accessible via IP
- [ ] Firewall configured
- [ ] SSL certificate installed (if using domain)
- [ ] Backup script created
- [ ] Monitoring setup
- [ ] DRY_RUN set to False for live trading

## 💡 Cost Breakdown

| Item | Cost |
|------|------|
| Hostinger VPS KVM 1 | ₹299/month |
| Domain (optional) | ₹99/year |
| SSL Certificate | Free (Let's Encrypt) |
| **Total** | **₹299/month** |

## 🚀 Going Live

Once everything is tested:

1. **Stop the bot**
   ```bash
   ./stop.sh
   ```

2. **Set DRY_RUN to False**
   ```bash
   nano src/config.py
   # Change: DRY_RUN = False
   ```

3. **Restart bot**
   ```bash
   ./restart.sh
   ```

4. **Monitor closely**
   ```bash
   ./logs.sh
   ```

**Your bot is now live on Hostinger VPS! 🎉**
