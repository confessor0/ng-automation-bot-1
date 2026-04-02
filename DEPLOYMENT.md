# NG Auto-Bot Deployment Guide

To deploy this application to **mkmoghal.com**, you need a server (VPS) and to point your domain to it.

## 1. Prepare the Server (Ubuntu/Debian recommended)
1.  Get a VPS (DigitalOcean, AWS, Hetzner, etc.).
2.  Point `A Record` for `mkmoghal.com` to your VPS IP Address.

## 2. Docker Deployment (Recommended)
Build and run the container on your server:

```bash
# 1. Upload code to server
scp -r "NG Automation" user@mkmoghal.com:~/app

# 2. Build Docker Image
cd ~/app
docker build -t ng-bot .

# 3. Run Container (Background)
# Map port 5050 to 80 (HTTP)
docker run -d \
  -p 80:5050 \
  --env-file .env \
  --name ng-bot-instance \
  ng-bot
```

## 3. Secure Access (Important)
Currently, the dashboard has **NO PASSWORD**.
Before deploying to a public domain, you **MUST** enable authentication in `src/web_server.py` or use Nginx with Basic Auth.

### Nginx Config (Example with Auth)
```nginx
server {
    listen 80;
    server_name mkmoghal.com;

    location / {
        proxy_pass http://localhost:8080;
        auth_basic "Restricted Area";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
}
```

## 4. Keeping it Alive
The `bot.py` is designed to run continuously. Using Docker with `-d` (detach) or `restart: always` ensures it stays up.
