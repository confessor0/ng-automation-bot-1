---
description: How to push the current code to a new GitHub repository
---

# How to Push Code to GitHub

Since `git` is not currently installed or recognized in your terminal, follow these steps to get your code onto GitHub.

## Step 1: Install Git
1.  **Option A (Automatic):** Run the following command in your terminal (PowerShell):
    ```powershell
    winget install -e --id Git.Git
    ```
    *Accept any prompts that appear.*
    *After installation, **RESTART YOUR TERMINAL** (close and reopen VS Code) for the command to work.*
    
    *Note: On your current machine, Git is located at `C:\Program Files\Git\bin\git.exe` if you need to access it directly.*

2.  **Option B (Manual):** Download and install from [git-scm.com](https://git-scm.com/download/win).

## Step 2: Create a GitHub Repository
1.  Log in to [GitHub.com](https://github.com).
2.  Click the **+** icon in the top right -> **New repository**.
3.  **Repository name:** `ng-automation-bot` (or your preferred name).
4.  **Privacy:** Private (recommended for trading bots).
5.  **Do NOT** initialize with README, .gitignore, or License (keep it empty).
6.  Click **Create repository**.

## Step 3: Initialize and Push
Once Git is installed and your terminal is restarted, run these commands in your project folder:

```bash
# 1. Initialize Git
git init

# 2. Add all files
git add .

# 3. Create first commit
git commit -m "Initial commit: NG Automation Bot"

# 4. Rename branch to main
git branch -M main

# 5. Connect to your new GitHub Repo
# REPLACE THE URL BELOW with your actual repo URL (e.g., https://github.com/username/ng-automation-bot.git)
git remote add origin https://github.com/YOUR_USERNAME/ng-automation-bot.git

# 6. Push code
git push -u origin main
```

## Step 4: Verify
Refresh your GitHub repository page. You should see all your files listed there.

## Next Step: Deploy to Railway
Once the code is on GitHub:
1.  Go to [Railway.app](https://railway.app).
2.  Click "New Project" -> "Deploy from GitHub repo".
3.  Select `ng-automation-bot`.
4.  Add your variables as described in the Railway guide.
