# Auto-Trader — Saxo Bank OpenAPI Bot

Automated trading bot skeleton for the Saxo Bank OpenAPI (SIM environment).

## Features

- **OAuth 2.0 Authorization Code Grant** — browser login with automatic token caching and silent refresh
- **Session capability upgrade** — requests `FullTradingAndChat` and polls every 60s to detect/fix downgrades
- **Async HTTP client** — built on `httpx.AsyncClient` with auto-retry on 401
- **SEMI mode** — interactive terminal menu with order confirmation before placement
- **Risk control** — hard $200 premium cap per order + $4 commission included in cost estimates

## Project Structure

```
├── .env/sim.env          # Credentials (never committed)
├── .env.example          # Credential template
├── .gitignore
├── config.py             # ENV switch + credential loading
├── requirements.txt
├── auth/
│   └── oauth.py          # OAuth flow + refresh + token cache
├── client/
│   └── saxo_client.py    # Async HTTP wrapper + background tasks
├── api/
│   ├── account.py        # get_me / get_balance / get_positions
│   ├── market_data.py    # search_instrument / get_quote / get_option_chain
│   └── orders.py         # place_order / cancel_order / get_open_orders
└── main.py               # Entry point — SEMI-auto terminal menu
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add your Saxo SIM credentials
cp .env.example .env/sim.env
# Edit .env/sim.env with your AppKey and AppSecret

# 3. Run
python main.py
```

On first run the bot opens your browser for Saxo login. After authentication, tokens are cached in `.tokens/` and reused automatically.

## Terminal Menu (SEMI mode)

```
========================================
 SAXO BOT — SIM Environment
========================================
[1] Account balance & positions
[2] Get live quote (search → quote)
[3] Manual order entry
[4] View open orders / cancel an order
[5] Refresh Session Capability now
[0] Exit
========================================
```

## Configuration

| Variable    | Default | Description                                |
| ----------- | ------- | ------------------------------------------ |
| `SAXO_ENV`  | `SIM`   | `SIM` or `LIVE`                            |
| `SAXO_MODE` | `SEMI`  | `SEMI` (confirm orders) or `AUTO` (future) |

Set via environment variables or edit `config.py` directly.

## Requirements

- Python 3.11+
- `httpx`, `python-dotenv`, `aioconsole` (see `requirements.txt`)
- A registered Saxo Bank OpenAPI application with redirect URI `http://localhost:3001/callback`

## API Endpoints Used

| Action            | Method   | Path                                       |
| ----------------- | -------- | ------------------------------------------ |
| Upgrade session   | `PATCH`  | `/root/v1/sessions/capabilities`           |
| Poll session      | `GET`    | `/root/v1/sessions/capabilities`           |
| User info         | `GET`    | `/port/v1/users/me`                        |
| Balance           | `GET`    | `/port/v1/balances/me`                     |
| Positions         | `GET`    | `/port/v1/positions/me`                    |
| Search instrument | `GET`    | `/ref/v1/instruments`                      |
| Live quote        | `GET`    | `/trade/v1/infoprices`                     |
| Place order       | `POST`   | `/trade/v2/orders`                         |
| Cancel order      | `DELETE` | `/trade/v2/orders/{OrderIds}`              |
| Open orders       | `GET`    | `/port/v1/orders/me`                       |
| Option chain      | `GET`    | `/ref/v1/instruments/contractoptionspaces` |
