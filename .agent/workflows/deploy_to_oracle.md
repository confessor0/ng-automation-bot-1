---
description: Deploying NG Automation Bot to Oracle Cloud Free Tier
---

# Deploying to Oracle Cloud Free Tier (Ubuntu VM)

This guide outlines the steps to deploy your NG Automation Bot to an Oracle Cloud "Always Free" VM.

## Prerequisites
1.  **Oracle Cloud Account**: Sign up at [cloud.oracle.com](https://cloud.oracle.com).
2.  **SSH Key Pair**: You will need this to connect to the server (Oracle generates one for you during setup if needed).
3.  **Fyers credentials**: Have your `APP_ID`, `SECRET_ID`, etc., ready.

## Step 1: Create the VM Instance

1.  Log in to Oracle Cloud Console.
2.  Navigate to **Compute** -> **Instances**.
3.  Click **Create Instance**.
4.  **Name**: `ng-trading-bot`
5.  **Image & Shape**:
    -   **Image**: Canonical Ubuntu 22.04 (or 24.04).
    -   **Shape**: Select **Ampere (ARM)** shape (VM.Standard.A1.Flex) if available (it allows up to 4 OCPUs and 24GB RAM for free!). If not, the AMD Micro instance is also fine but less powerful.
6.  **Networking**: Ensure "Assign a public IPv4 address" is checked.
7.  **SSH Keys**: Download the private key (`.key` file) provided by Oracle (SAVE THIS SECURELY!).
8.  Click **Create**.

## Step 2: Connect to the VM

Open your terminal (PowerShell on Windows or Terminal on Mac).
Move the key file to a safe folder (e.g., `C:\Keys\ssh-key-2024.key`).

```powershell
# Set permissions (only needed on Linux/Mac, or use PuttyGen on Windows if typical SSH fails)
# For Windows PowerShell standard ssh:
ssh -i "path\to\your\private.key" ubuntu@<YOUR_PUBLIC_IP_ADDRESS>
```
*Note: Replace `<YOUR_PUBLIC_IP_ADDRESS>` with the IP found on the Instance details page.*

## Step 3: Server Setup (Run these commands on the Server)

Update the system and install Python/pip.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv git screen -y
```

## Step 4: Deploy the Code

1.  **Clone/Upload Code**:
    *   *Option A (Git)*: Push your code to a private GitHub repo, then clone it here.
    *   *Option B (SCP)*: Copy files from your local PC to the server.
    ```powershell
    # Run from your LOCAL machine
    scp -i "path\key.key" -r "C:\Users\ahmed\Desktop\NG Automation" ubuntu@<IP>:~/ng_bot
    ```

2.  **Setup Virtual Environment**:
    ```bash
    cd ~/ng_bot
    python3 -m venv venv
    source venv/bin/activate
    
    # Install dependencies
    pip install -r requirements.txt
    pip install flask gunicorn  # Needed for web server production
    ```

## Step 5: Configure Environment

Create the `.env` file with your credentials.

```bash
nano .env
```
*Paste your `.env` content here. Ctrl+O, Enter to save. Ctrl+X to exit.*

## Step 6: Create System Service (Run automatically on boot)

We will use `systemd` to keep the bot running 24/7.

1.  Create service file:
    ```bash
    sudo nano /etc/systemd/system/ng-bot.service
    ```

2.  Paste the following (adjust paths if needed):
    ```ini
    [Unit]
    Description=NG Trading Bot
    After=network.target

    [Service]
    User=ubuntu
    WorkingDirectory=/home/ubuntu/ng_bot
    ExecStart=/home/ubuntu/ng_bot/venv/bin/python bot.py
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target
    ```

3.  Start it:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable ng-bot
    sudo systemctl start ng-bot
    ```

4.  Check status:
    ```bash
    sudo systemctl status ng-bot
    ```

## Step 7: Access the Dashboard

1.  **Open Firewall Port (Oracle Security List)**:
    -   Go to Oracle Console -> Networking -> Virtual Cloud Networks -> Click your VCN -> Security Lists -> Default Security List.
    -   Add **Ingress Rule**:
        -   Source CIDR: `0.0.0.0/0` (or your specific home IP for security).
        -   Protocol: TCP.
        -   Destination Port Range: `5055`.
2.  **Open Firewall (Ubuntu)**:
    ```bash
    sudo iptables -F
    sudo netfilter-persistent save
    ```
    *(Or allow specific port)*: `sudo ufw allow 5055`

3.  **Visit**: `http://<YOUR_PUBLIC_IP>:5055`

## Step 8: Maintenance

-   **View Logs**: `journalctl -u ng-bot -f`
-   **Restart Bot**: `sudo systemctl restart ng-bot`
