#!/bin/bash
# NG Automation Bot - Hostinger VPS Setup Script
# Optimized for Hostinger's Ubuntu VPS
# Run as: bash hostinger_setup.sh

set -e

echo "🚀 NG Mini Bot - Hostinger VPS Deployment"
echo "=========================================="

# Update system
echo "📦 Updating system..."
sudo apt update && sudo apt upgrade -y

# Install essential packages
echo "🔧 Installing dependencies..."
sudo apt install -y python3.11 python3.11-venv python3-pip git nginx ufw

# Create application directory
echo "📁 Creating application directory..."
sudo mkdir -p /var/www/ng-automation
sudo chown -R $USER:$USER /var/www/ng-automation
cd /var/www/ng-automation

# Upload your code (you'll do this via SFTP or Git)
echo "📥 Ready for code upload..."
echo "   Upload your code to: /var/www/ng-automation"
echo "   Or clone from Git: git clone YOUR_REPO_URL ."

# Create virtual environment
echo "🐍 Setting up Python environment..."
python3.11 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "📚 Installing Python packages..."
pip install --upgrade pip
pip install flask gunicorn pandas numpy yfinance schedule colorama fyers-apiv3 python-dotenv

# Create .env template
echo "🔐 Creating .env template..."
cat > .env << 'EOF'
# Fyers API Credentials
FYERS_APP_ID=your_app_id_here
FYERS_SECRET_ID=your_secret_key_here
FYERS_USER_ID=your_user_id_here
FYERS_REDIRECT_URI=https://trade.fyers.in/api-login/redirect-uri/index.html
FYERS_ACCESS_TOKEN=your_access_token_here
EOF

echo ""
echo "⚠️  IMPORTANT: Edit .env file with your credentials"
echo "   Run: nano /var/www/ng-automation/.env"
echo ""

# Create systemd service
echo "⚙️  Creating systemd service..."
sudo tee /etc/systemd/system/ng-bot.service > /dev/null << EOF
[Unit]
Description=NG Mini Trading Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/var/www/ng-automation
Environment="PATH=/var/www/ng-automation/venv/bin"
ExecStart=/var/www/ng-automation/venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/ng-bot.log
StandardError=append:/var/log/ng-bot-error.log

[Install]
WantedBy=multi-user.target
EOF

# Create log files
sudo touch /var/log/ng-bot.log /var/log/ng-bot-error.log
sudo chown $USER:$USER /var/log/ng-bot*.log

# Configure Nginx reverse proxy
echo "🌐 Configuring Nginx..."
sudo tee /etc/nginx/sites-available/ng-bot > /dev/null << 'EOF'
server {
    listen 80;
    server_name _;

    # Increase timeouts for long-polling
    proxy_read_timeout 300s;
    proxy_connect_timeout 75s;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
    }

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
EOF

sudo ln -sf /etc/nginx/sites-available/ng-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

# Configure firewall
echo "🔥 Configuring firewall..."
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS (for future SSL)
sudo ufw --force enable

# Enable services
echo "🎯 Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable ng-bot
sudo systemctl enable nginx

# Create management scripts
echo "📝 Creating management scripts..."

# Start script
cat > /var/www/ng-automation/start.sh << 'EOF'
#!/bin/bash
sudo systemctl start ng-bot
sudo systemctl status ng-bot
EOF
chmod +x /var/www/ng-automation/start.sh

# Stop script
cat > /var/www/ng-automation/stop.sh << 'EOF'
#!/bin/bash
sudo systemctl stop ng-bot
echo "Bot stopped"
EOF
chmod +x /var/www/ng-automation/stop.sh

# Restart script
cat > /var/www/ng-automation/restart.sh << 'EOF'
#!/bin/bash
sudo systemctl restart ng-bot
echo "Bot restarted"
sudo systemctl status ng-bot
EOF
chmod +x /var/www/ng-automation/restart.sh

# View logs script
cat > /var/www/ng-automation/logs.sh << 'EOF'
#!/bin/bash
echo "=== Live Logs (Ctrl+C to exit) ==="
sudo journalctl -u ng-bot -f
EOF
chmod +x /var/www/ng-automation/logs.sh

# Status script
cat > /var/www/ng-automation/status.sh << 'EOF'
#!/bin/bash
echo "=== Bot Status ==="
sudo systemctl status ng-bot
echo ""
echo "=== Recent Logs ==="
sudo journalctl -u ng-bot -n 20 --no-pager
EOF
chmod +x /var/www/ng-automation/status.sh

# Create update script
cat > /var/www/ng-automation/update.sh << 'EOF'
#!/bin/bash
echo "🔄 Updating bot..."
cd /var/www/ng-automation
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart ng-bot
echo "✅ Update complete!"
EOF
chmod +x /var/www/ng-automation/update.sh

echo ""
echo "✅ Hostinger VPS Setup Complete!"
echo "=================================="
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Upload your code:"
echo "   scp -r 'C:/Users/ahmed/Desktop/NG Automation/*' user@YOUR_VPS_IP:/var/www/ng-automation/"
echo ""
echo "2. Edit credentials:"
echo "   nano /var/www/ng-automation/.env"
echo ""
echo "3. Start the bot:"
echo "   cd /var/www/ng-automation"
echo "   ./start.sh"
echo ""
echo "4. Access dashboard:"
echo "   http://YOUR_VPS_IP"
echo ""
echo "📊 Management Commands:"
echo "   ./start.sh    - Start the bot"
echo "   ./stop.sh     - Stop the bot"
echo "   ./restart.sh  - Restart the bot"
echo "   ./logs.sh     - View live logs"
echo "   ./status.sh   - Check bot status"
echo "   ./update.sh   - Update from Git"
echo ""
echo "🔒 Optional: Setup SSL Certificate"
echo "   sudo apt install certbot python3-certbot-nginx"
echo "   sudo certbot --nginx -d yourdomain.com"
echo ""
echo "💡 Hostinger Panel: https://hpanel.hostinger.com"
