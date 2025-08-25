from dotenv import load_dotenv
import os
from binance.um_futures import UMFutures

# 1) حمّل بيانات .env
load_dotenv()
key = os.getenv("BINANCE_API_KEY")
secret = os.getenv("BINANCE_API_SECRET")

# 2) اتصل بـ Binance Testnet
client = UMFutures(
    key,
    secret,
    base_url="https://testnet.binancefuture.com"  # API testnet
)

# 3) اطبع الرصيد
try:
    balances = client.balance()
    usdt_balance = next(b for b in balances if b["asset"] == "USDT")
    print(f"✅ Ready! Testnet balance = {usdt_balance['balance']} USDT")

except Exception as e:
    print("❌ Error:", e)
