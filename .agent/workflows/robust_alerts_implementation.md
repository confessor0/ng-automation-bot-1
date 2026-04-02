---
description: Implementation Plan for Robust Alert Notification System
---

# Robust Alert Notification System Implementation Plan

This plan outlines the steps to upgrade the notification system to support multiple providers (Telegram, WhatsApp, Email, Sound) with configurable options via the Admin Panel.

## 1. Architecture Overhaul ("Notification Hub")
Create a centralized `NotificationDispatcher` class that manages multiple providers and routes alerts based on configuration and alert severity.

- **File**: `src/notifications/dispatcher.py` (New File)
- **Role**: Subscribe to `StateManager` alerts/logs and dispatch to enabled providers.
- **Features**:
  - Support for multiple channels (Telegram, WhatsApp, Email, Sound).
  - Toggles for each channel (Enable/Disable).
  - Severity filtering (e.g., WhatsApp only for CRITICAL).

## 2. Notification Providers
Implement separate classes/functions for each provider in `src/notifications/providers/`.

### A. Telegram (Enhancement)
- **File**: `src/notifications/providers/telegram_provider.py`
- **Features**: Refine existing logic, ensure connection resilience, formatted messages (HTML/Markdown).

### B. WhatsApp (New)
- **File**: `src/notifications/providers/whatsapp_provider.py`
- **Tech**: Use `Twilio` API (Industry Standard) or a simple URL-based intent link for manual sending (if free). For robust automation, Twilio is recommended.
- **Config**: `TWILIO_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM`, `WHATSAPP_TO`.

### C. Email (New)
- **File**: `src/notifications/providers/email_provider.py`
- **Tech**: `smtplib` for standard SMTP (Gmail/Outlook).
- **Config**: `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_TO`.

### D. System Sounds (Frontend)
- **File**: `templates/index.html` (Javascript)
- **Tech**: Browser `Audio()` API.
- **Logic**: Dashboard polls for new alerts. If a new CRITICAL/WARNING alert arrives, play respective sound file (e.g., `siren.mp3`, `chime.mp3`).

## 3. Storage & Configuration
Update `products` options to manage credentials securely.

- **Storage**: `secrets/notifications.json` (or encrypted field in state).
- **Admin Panel UI**:
  - Add "Notifications" tab in Admin section.
  - Forms for configuring keys (SMTP, Twilio, Telegram).
  - Test buttons for each channel.
  - Toggles: "Enable Email", "Enable WhatsApp", "Enable Sound".

## 4. Integration with Core Logic
- **Hook Point**: `src/state_manager.py` -> `add_alert()`
- **Logic**: When `add_alert` is called:
  1. Store in internal list (existing).
  2. Call `NotificationDispatcher.broadcast(alert)`.

## 5. Step-by-Step Execution Plan

1.  **Refactor Directory Structure**:
    - Create `src/notifications/` folder.
    - Move `src/notification_service.py` logic to `src/notifications/dispatcher.py`.

2.  **Implement Providers**:
    - Write `EmailProvider` class.
    - Write `WhatsAppProvider` class (Twilio stub or implementation).
    - Update `TelegramProvider`.

3.  **Update State & Config**:
    - Add `notification_config` to `StateManager`.
    - Create API endpoints in `web_server.py` to save/load notification settings.

4.  **Frontend Updates**:
    - Add "Notifications" section to Admin Panel (`index.html`).
    - Implement Sound Alert logic in JS (Polling loop checks for priority alerts).

5.  **Testing**:
    - running `verify_notifications.py` to test all channels.

## 6. Dependencies
- `twilio` (for WhatsApp)
- `smtplib` (Standard Lib)
- `playsound` (Optional for backend sound, but Frontend sound is better for web apps)

## 7. Immediate Action Request
- Run `pip install twilio` if WhatsApp is confirmed.
- Confirm SMTP details with user or provide Gmail App Password instructions.
