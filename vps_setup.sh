#!/bin/bash
# NG Automation Bot - VPS Setup Script
# Run this on a fresh Ubuntu 22.04 VPS

set -e

echo "🚀 NG Mini Automation Bot - VPS Setup"
echo "======================================"

# Update system
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
echo "🐍 Installing Python 3.11..."
sudo apt install -y python3.11 python3.11-venv python3-pip

# Install Docker (optional, for containerized deployment)
echo "🐳 Installing Docker..."
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Git
echo "📂 Installing Git..."
sudo apt install -y git

# Clone repository (update with your repo URL)
echo "📥 Cloning repository..."
cd /opt
sudo git clone https://github.com/yourusername/ng-automation.git
cd ng-automation

# Create virtual environment
echo "🔧 Setting up Python environment..."
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
echo "📚 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
echo "🔐 Creating environment file..."
cat > .env << EOF
FYERS_APP_ID=your_app_id
FYERS_SECRET_ID=your_secret_id
FYERS_USER_ID=your_user_id
FYERS_REDIRECT_URI=your_redirect_uri
FYERS_ACCESS_TOKEN=your_access_token
EOF

echo "⚠️  IMPORTANT: Edit /opt/ng-automation/.env with your actual credentials"

# Create systemd service for auto-start
echo "⚙️  Creating systemd service..."
sudo cat > /etc/systemd/system/ng-bot.service << EOF
[Unit]
Description=NG Mini Trading Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/ng-automation
Environment="PATH=/opt/ng-automation/venv/bin"
ExecStart=/opt/ng-automation/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
echo "🎯 Enabling auto-start..."
sudo systemctl daemon-reload
sudo systemctl enable ng-bot.service

# Setup firewall
echo "🔥 Configuring firewall..."
sudo ufw allow 5050/tcp
sudo ufw allow 22/tcp
sudo ufw --force enable

# Install Nginx as reverse proxy
echo "🌐 Installing Nginx..."
sudo apt install -y nginx

# Configure Nginx
sudo cat > /etc/nginx/sites-available/ng-bot << EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_cache_bypass \$http_upgrade;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/ng-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "✅ Setup Complete!"
echo "===================="
echo ""
echo "Next Steps:"
echo "1. Edit credentials: sudo nano /opt/ng-automation/.env"
echo "2. Start the bot: sudo systemctl start ng-bot"
echo "3. Check status: sudo systemctl status ng-bot"
echo "4. View logs: sudo journalctl -u ng-bot -f"
echo "5. Access dashboard: http://YOUR_VPS_IP"
echo ""
echo "🔒 Security: Consider setting up SSL with Let's Encrypt"
echo "   sudo apt install certbot python3-certbot-nginx"
echo "   sudo certbot --nginx -d yourdomain.com"
