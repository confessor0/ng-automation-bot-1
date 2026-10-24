# NG Automation Bot — Current Application Scope

## Supported product

The application is configured for Natural Gas Mini. Fyers access uses browser OAuth and an access token that is validated before market data is used. If the session expires, reconnect through the Admin panel.

## Dashboard and workflow

The web dashboard provides market and strategy status, on-demand refresh, an order console, live orders and positions, risk monitoring, adjustment controls, emergency actions, and an audit log. Strategy 1 can produce an order intent for validation and sequential execution. Strategy 2 currently produces signal-only results; its executable legs and quantities have not been configured.

## Admin settings

The Admin panel contains the manual News / Event Risk interlock, active signal strategy selection, Fyers browser login/logout, Mailjet trend notification settings, risk limits, emergency controls, and audit export. Critical alerts remain visible in the dashboard.

## Removed from the application

The standalone test page, P&L simulator, Positions Analyzer page and snapshot history, manual basket builder, user administration, Telegram notifications, browser popup/sound preferences, headless Fyers login, autonomous mode, and support for other instruments have been removed. Position analysis remains an internal dependency of risk adjustments.
