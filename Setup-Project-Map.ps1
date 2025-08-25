# === إنشاء مجلدات ===
New-Item -ItemType Directory -Force docs, strategies, core, config, tests | Out-Null

# === requirements.txt ===
@"
binance-connector==3.12.0
pandas==2.3.1
numpy==2.2.6
python-dotenv==1.1.1
"@ | Set-Content requirements.txt -Encoding UTF8

# === .env.example (لا تضع أسرار حقيقية) ===
@"
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
"@ | Set-Content .env.example -Encoding UTF8

# === .gitignore ===
@"
# Python
__pycache__/
*.pyc

# Env
.venv/
.env

# OS
.DS_Store
Thumbs.db
"@ | Set-Content .gitignore -Encoding UTF8

# === .editorconfig ===
@"
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 4
trim_trailing_whitespace = true
"@ | Set-Content .editorconfig -Encoding UTF8

# === docs/README.md ===
@"
# Binance USDⓈ-M Futures GUI Bot

- GUI (Tkinter) لإدارة إعدادات التداول بسهولة.
- Hedge Mode + خروج OCO محاكى (soft OCO).
- استراتيجيات: EMA/RSI، Breakout، Mean Reversion.
- Dry-Run للتجربة بدون أوامر حقيقية.

## تشغيل سريع
\`\`\`bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # عدّل المفاتيح
python binance_futures_gui_bot.py
\`\`\`

## هيكلة عامة
(شوف docs/ARCHITECTURE.md للمخطط)
"@ | Set-Content docs\README.md -Encoding UTF8

# === docs/ARCHITECTURE.md ===
@"
# Architecture

```mermaid
flowchart LR
  UI[TKinter GUI] -->|Queue| Worker[BotWorker Thread]
  Worker -->|UMFutures| Binance[(Binance Futures API)]
  Worker --> Indicators[EMA/RSI/ATR]
  Worker --> Orders[Market/Stop/TP (soft OCO)]
  Worker --> Status[UI Status Updates]
