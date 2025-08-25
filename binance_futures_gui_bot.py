"""
Binance USDⓈ-M Futures Bot — GUI (Tkinter) + Hedge Mode + OCO + Strategies + Dry-Run

⚠️ للتعليم فقط — اشتغل على TESTNET الأول. الكود يدعم التبديل للحساب الحقيقي.

المزايا الآن:
- واجهة رسومية كاملة لاختيار الإعدادات.
- Hedge Mode (تشغيل/إيقاف) + تحديد positionSide تلقائيًا.
- دخول/خروج بنمط OCO مُحاكى (soft OCO): نضع أمرين متعاكسين ونقوم بإلغاء القرين عند التفعيل/إغلاق المركز.
- استراتيجيات متعددة: EMA/RSI، Breakout HH/LL، Mean Reversion. (Grid: placeholder للتوسّع لاحقًا)
- Dry-Run: تشغيل تجريبي بدون إرسال أوامر فعلية (يُسجل ما كان سيتم إرساله).
- جاهز للحقيقي: مجرّد إزالة علامة TESTNET + تعطيل Dry-Run.

التثبيت:
    pip install binance-connector pandas numpy python-dotenv
ملف .env:
    BINANCE_API_KEY=your_key
    BINANCE_API_SECRET=your_secret
"""
from __future__ import annotations

# ===== stdlib =====
import os
import threading
import time
import queue
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple, List

# ===== third-party =====
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Binance USDⓈ-M Futures (من binance-connector)
# نستخدم UMFutures ونسميه FuturesClient لسهولة القراءة في الكود
from binance.um_futures import UMFutures as FuturesClient

# ===== GUI (Tkinter) =====
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import scrolledtext


# ============================ Core Logic ==============================
@dataclass
class Filters:
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Optional[Decimal]


def quantize(value: Decimal, step: Decimal) -> Decimal:
    if step == 0:
        return value
    return (value // step) * step


def str_dec(d: Decimal) -> str:
    return format(d.normalize(), 'f')


def fetch_exchange_filters(client: FuturesClient, symbol: str) -> Filters:
    info = client.exchange_info()
    sym = next(s for s in info["symbols"] if s["symbol"] == symbol)
    f_map = {f["filterType"]: f for f in sym["filters"]}
    tick = Decimal(f_map.get("PRICE_FILTER", {}).get("tickSize", "0.01"))
    lot = Decimal(f_map.get("LOT_SIZE", {}).get("stepSize", "0.001"))
    min_qty = Decimal(f_map.get("LOT_SIZE", {}).get("minQty", "0.0"))
    min_notional = None
    if "MIN_NOTIONAL" in f_map:
        min_notional = Decimal(
            f_map["MIN_NOTIONAL"].get("notional", f_map["MIN_NOTIONAL"].get("minNotional", "0"))
        )
    return Filters(tick, lot, min_qty, min_notional)


def klines_df(client: FuturesClient, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    kl = client.klines(symbol=symbol, interval=interval, limit=limit)
    cols = [
        "open_time", "open", "high", "low", "close", "volume", "close_time", "qav",
        "num_trades", "taker_base", "taker_quote", "ignore"
    ]
    df = pd.DataFrame(kl, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df

# ---------------------------- Indicators -----------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0.0)).ewm(alpha=1/length, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1/length, adjust=False).mean()

# ====================== Order Helpers (Futures) ======================

def place_market_entry(client: FuturesClient, symbol: str, side: str,
                       qty: Decimal, hedge: bool, log):
    """
    يدخل ماركت لونج/شورت. في وضع Hedge نحدد positionSide.
    side: "BUY" لفتح LONG، "SELL" لفتح SHORT
    """
    try:
        params = dict(symbol=symbol, side=side, type="MARKET", quantity=str_dec(qty))
        if hedge:
            params["positionSide"] = "LONG" if side == "BUY" else "SHORT"
        o = client.new_order(**params)
        log(f"✅ Opened {'LONG' if side=='BUY' else 'SHORT'} qty={qty}")
        return o
    except Exception as e:
        log(f"Open order error: {e}")
        raise


def cancel_all_open(client: FuturesClient, symbol: str, log):
    """يلغي جميع الأوامر المعلقة على الرمز (مفيد لمحاكاة OCO)."""
    try:
        client.cancel_all_open_orders(symbol=symbol)
        log("🧹 Canceled all open orders")
    except Exception as e:
        log(f"Cancel open orders error: {e}")


def place_soft_oco_exit(client: FuturesClient, symbol: str, entry_side: str,
                        entry_px: Decimal, stop_dist: Decimal, rr: Decimal,
                        f: Filters, working_type: str, hedge: bool, log):
    """
    خروج OCO مُحاكى: نضع STOP_MARKET + TAKE_PROFIT_MARKET (closePosition=True).
    entry_side: "BUY" لو المركز لونج، "SELL" لو المركز شورت.
    """
    if entry_side == "BUY":
        stop_px = quantize(entry_px - stop_dist, f.tick_size)
        tp_px   = quantize(entry_px + stop_dist * rr, f.tick_size)
        side_close = "SELL"
        pos_side   = "LONG" if hedge else None
    else:
        stop_px = quantize(entry_px + stop_dist, f.tick_size)
        tp_px   = quantize(entry_px - stop_dist * rr, f.tick_size)
        side_close = "BUY"
        pos_side   = "SHORT" if hedge else None

    # STOP_MARKET
    try:
        p = dict(symbol=symbol, side=side_close, type="STOP_MARKET",
                 stopPrice=str_dec(stop_px), closePosition=True,
                 workingType=working_type)
        if pos_side:
            p["positionSide"] = pos_side
        client.new_order(**p)
        log(f"⛔ STOP set @ {stop_px}")
    except Exception as e:
        log(f"STOP error: {e}")

    # TAKE_PROFIT_MARKET
    try:
        p = dict(symbol=symbol, side=side_close, type="TAKE_PROFIT_MARKET",
                 stopPrice=str_dec(tp_px), closePosition=True,
                 workingType=working_type)
        if pos_side:
            p["positionSide"] = pos_side
        client.new_order(**p)
        log(f"🎯 TP set @ {tp_px}")
    except Exception as e:
        log(f"TP error: {e}")


def place_soft_oco_entry_breakout(client: FuturesClient, symbol: str,
                                  hh: Decimal, ll: Decimal, qty: Decimal,
                                  f: Filters, working_type: str,
                                  hedge: bool, log):
    """
    دخول Breakout OCO مُحاكى: BUY STOP فوق أعلى قمة و SELL STOP تحت أدنى قاع.
    """
    buy_stop  = quantize(hh + f.tick_size, f.tick_size)
    sell_stop = quantize(ll - f.tick_size, f.tick_size)

    # BUY STOP
    try:
        p = dict(symbol=symbol, side="BUY", type="STOP_MARKET",
                 stopPrice=str_dec(buy_stop), quantity=str_dec(qty),
                 workingType=working_type)
        if hedge:
            p["positionSide"] = "LONG"
        client.new_order(**p)
        log(f"📈 ENTRY BUY STOP @ {buy_stop}")
    except Exception as e:
        log(f"Entry BUY STOP error: {e}")

    # SELL STOP
    try:
        p = dict(symbol=symbol, side="SELL", type="STOP_MARKET",
                 stopPrice=str_dec(sell_stop), quantity=str_dec(qty),
                 workingType=working_type)
        if hedge:
            p["positionSide"] = "SHORT"
        client.new_order(**p)
        log(f"📉 ENTRY SELL STOP @ {sell_stop}")
    except Exception as e:
        log(f"Entry SELL STOP error: {e}")

# =========================== Account Helpers =========================

def apply_leverage_margin(client: FuturesClient, symbol: str, leverage: int, margin_type: str, log):
    """يضبط الرافعة ونوع المارجن للرمز"""
    try:
        res = client.change_leverage(symbol=symbol, leverage=leverage)
        log(f"Leverage set: {res.get('leverage')}x")
    except Exception as e:
        log(f"⚠️ leverage error: {e}")

    try:
        res = client.change_margin_type(symbol=symbol, marginType=margin_type.upper())
        log(f"Margin type set: {margin_type}")
    except Exception as e:
        if "No need to change margin type" in str(e):
            log(f"Margin type already {margin_type}")
        else:
            log(f"⚠️ margin error: {e}")


def ensure_position_mode(client: FuturesClient, hedge: bool, log):
    """يضبط وضع الهيدج أو One-Way"""
    try:
        mode = "true" if hedge else "false"
        client.change_position_mode(dualSidePosition=mode)
        log(f"Position mode set: {'HEDGE' if hedge else 'ONE-WAY'}")
    except Exception as e:
        if "No need to change position side" in str(e):
            log(f"Position mode already {'HEDGE' if hedge else 'ONE-WAY'}")
        else:
            log(f"⚠️ position mode error: {e}")


def account_position_amt(client: FuturesClient, symbol: str, hedge: bool) -> Tuple[Decimal, Decimal]:
    """
    يرجّع كميات المركز:
      - في وضع Hedge: (long_amt, short_amt)
      - في One-Way:   (net_amt, 0)
    """
    try:
        acc = client.account()
        positions = acc.get("positions", [])
    except Exception:
        try:
            pr = client.position_risk(symbol=symbol)
            positions = pr if isinstance(pr, list) else [pr]
        except Exception:
            return Decimal("0"), Decimal("0")

    long_amt = Decimal("0")
    short_amt = Decimal("0")
    net_amt = Decimal("0")

    for p in positions:
        if p.get("symbol") != symbol:
            continue
        side = p.get("positionSide", "BOTH")
        amt  = Decimal(p.get("positionAmt", "0"))

        if hedge:
            if side == "LONG":
                long_amt = amt
            elif side == "SHORT":
                short_amt = amt
        else:
            net_amt = amt
            break

    if hedge:
        return long_amt, short_amt
    else:
        return net_amt, Decimal("0")


def mark_price(client: FuturesClient, symbol: str) -> Decimal:
    """إرجاع الـ Mark Price لرمز معين كـ Decimal."""
    try:
        data = client.mark_price(symbol=symbol)
        return Decimal(data["markPrice"])
    except Exception as e:
        print(f"⚠️ mark_price error: {e}")
        return Decimal("0")


# =========================== Worker Thread ===========================
class BotWorker:
    def __init__(self, cfg: dict, ui_queue: queue.Queue):
        self.cfg = cfg
        self.ui_queue = ui_queue
        self._stop = threading.Event()
        self.thread = None

    def log(self, msg: str):
        self.ui_queue.put(("log", msg))

    def status(self, data: dict):
        self.ui_queue.put(("status", data))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()

    def running(self):
        return self.thread and self.thread.is_alive()

    # ------------------------------ RUN ------------------------------
    def run(self):
        import traceback
        from pathlib import Path

        try:
            # 1) حمّل .env من نفس مجلد السكربت
            load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
            self.log("🔧 init…")

            # 2) المفاتيح
            key = os.getenv("BINANCE_API_KEY")
            secret = os.getenv("BINANCE_API_SECRET")
            if not key or not secret:
                self.log("❌ API keys مفقودة في .env (BINANCE_API_KEY / BINANCE_API_SECRET).")
                return

            # 3) أنشئ العميل (مهم: معاملات موضعية)
            base_url = "https://testnet.binancefuture.com" if self.cfg["testnet"] else None
            client = FuturesClient(key=key, secret=secret, base_url=base_url)

            # 4) رصيد للتأكد
            try:
                bals = client.balance()
                asset_row = (next((b for b in bals if b.get("asset") == "USDT"), None)
                            or next((b for b in bals if b.get("asset") == "FDUSD"), None)
                            or next((b for b in bals if b.get("asset") == "BUSD"), None)
                            or (bals[0] if bals else None))

                if asset_row:
                    asset   = asset_row.get("asset", "?")
                    wallet  = asset_row.get("balance", "0")
                    avail   = asset_row.get("availableBalance", asset_row.get("crossWalletBalance", "0"))
                    max_wd  = asset_row.get("maxWithdrawAmount", "?")
                    margin  = asset_row.get("marginBalance", asset_row.get("crossWalletBalance", "?"))

                    self.log(f"✅ Ready! {asset} | Wallet={wallet} | Avail={avail} | MaxWD={max_wd} | Margin={margin}")
                else:
                    self.log("✅ Ready! (لم أتمكن من قراءة أي رصيد من API)")
            except Exception as e:
                self.log(f"⚠️ balance err: {e}")

            # 5) إعدادات
            symbol = self.cfg["symbol"]
            interval = self.cfg["interval"]
            lookback = int(self.cfg["lookback"])
            leverage = int(self.cfg["leverage"])
            margin_type = self.cfg["margin_type"]
            position_usdt = Decimal(str(self.cfg["position_usdt"]))
            rsi_long = float(self.cfg["rsi_long"])
            rsi_short = float(self.cfg["rsi_short"])
            atr_mult = Decimal(str(self.cfg["atr_mult"]))
            rr = Decimal(str(self.cfg["rr"]))
            poll = float(self.cfg["poll_seconds"]) or 10
            working_type = self.cfg["working_type"]
            hedge = bool(self.cfg["hedge_mode"])
            dry_run = bool(self.cfg["dry_run"])
            strategy = self.cfg["strategy"]
            breakout_lb = int(self.cfg.get("breakout_lookback", 20))
            mr_rsi_low = float(self.cfg.get("mr_rsi_low", 30))
            mr_rsi_high = float(self.cfg.get("mr_rsi_high", 70))

            # 6) فلاتر + تهيئة
            f = fetch_exchange_filters(client, symbol)
            self.log(f"Filters: tick={f.tick_size} step={f.step_size} minQty={f.min_qty}")

            apply_leverage_margin(client, symbol, leverage, margin_type, self.log)
            ensure_position_mode(client, hedge, self.log)

            # 7) الحلقة
            while not self._stop.is_set():
                try:
                    long_amt, short_amt = account_position_amt(client, symbol, hedge)
                    in_pos = (long_amt != 0 or short_amt != 0) if hedge else (long_amt != 0)

                    df = klines_df(client, symbol, interval, lookback)
                    df["ema_fast"] = ema(df["close"], 20)
                    df["ema_slow"] = ema(df["close"], 50)
                    df["rsi"] = rsi(df["close"], 14)
                    df["atr"] = atr(df, 14)
                    last = df.iloc[-1]
                    ema_fast = float(last["ema_fast"]); ema_slow = float(last["ema_slow"])
                    rsi_last = float(last["rsi"])
                    atr_last = Decimal(str(max(last["atr"], 0.0)))
                    mpx = mark_price(client, symbol)

                    qty = Decimal("0")
                    if mpx > 0:
                        qty = quantize(max(Decimal("0.0"), (position_usdt / mpx)), f.step_size)
                        if qty < f.min_qty:
                            qty = quantize(f.min_qty, f.step_size)

                    long_sig = short_sig = False
                    if strategy == "EMA_RSI":
                        long_sig = (ema_fast > ema_slow) and (rsi_last > rsi_long)
                        short_sig = (ema_fast < ema_slow) and (rsi_last < rsi_short)
                    elif strategy == "MEANREV":
                        long_sig = rsi_last < mr_rsi_low
                        short_sig = rsi_last > mr_rsi_high
                    elif strategy == "BREAKOUT":
                        pass

                    self.status({
                        "mark": str(mpx),
                        "ema20": f"{ema_fast:.2f}",
                        "ema50": f"{ema_slow:.2f}",
                        "rsi": f"{rsi_last:.1f}",
                        "atr": str(atr_last),
                        "qty": str(qty),
                        "pos": f"L={long_amt} S={short_amt}" if hedge else str(long_amt),
                        "long_sig": long_sig,
                        "short_sig": short_sig,
                        "strategy": strategy,
                    })

                    if dry_run:
                        if strategy in ("EMA_RSI","MEANREV") and not in_pos and qty > 0:
                            if long_sig:
                                self.log(f"[DRY] Would OPEN LONG qty={qty} @~{mpx}")
                                self.log(f"[DRY] Would set STOP/TP with ATRx{atr_mult} and RR={rr}")
                            elif short_sig:
                                self.log(f"[DRY] Would OPEN SHORT qty={qty} @~{mpx}")
                                self.log(f"[DRY] Would set STOP/TP with ATRx{atr_mult} and RR={rr}")
                        if strategy == "BREAKOUT" and not in_pos and qty > 0:
                            import pandas as pd
                            hh = Decimal(str(pd.Series(df["high"]).tail(breakout_lb).max()))
                            ll = Decimal(str(pd.Series(df["low"]).tail(breakout_lb).min()))
                            self.log(f"[DRY] Would place OCO ENTRY: BUY_STOP>{hh} & SELL_STOP<{ll}")
                        time.sleep(poll)
                        continue

                    if strategy in ("EMA_RSI","MEANREV") and not in_pos and qty > 0:
                        if long_sig:
                            try:
                                place_market_entry(client, symbol, "BUY", qty, hedge, self.log)
                                stop_dist = atr_last * atr_mult
                                place_soft_oco_exit(client, symbol, "BUY", mpx, stop_dist, rr, f, working_type, hedge, self.log)
                            except Exception as e:
                                self.log(f"Open LONG error: {e}")
                        elif short_sig:
                            try:
                                place_market_entry(client, symbol, "SELL", qty, hedge, self.log)
                                stop_dist = atr_last * atr_mult
                                place_soft_oco_exit(client, symbol, "SELL", mpx, stop_dist, rr, f, working_type, hedge, self.log)
                            except Exception as e:
                                self.log(f"Open SHORT error: {e}")

                    if strategy == "BREAKOUT" and qty > 0:
                        if not in_pos:
                            import pandas as pd
                            hh = Decimal(str(pd.Series(df["high"]).tail(breakout_lb).max()))
                            ll = Decimal(str(pd.Series(df["low"]).tail(breakout_lb).min()))
                            place_soft_oco_entry_breakout(client, symbol, hh, ll, qty, f, working_type, hedge, self.log)
                        else:
                            cancel_all_open(client, symbol, self.log)
                            net = long_amt - abs(short_amt) if hedge else long_amt
                            if net > 0:
                                place_soft_oco_exit(client, symbol, "BUY", mpx, atr_last * atr_mult, rr, f, working_type, hedge, self.log)
                            elif net < 0:
                                place_soft_oco_exit(client, symbol, "SELL", mpx, atr_last * atr_mult, rr, f, working_type, hedge, self.log)

                    long_amt2, short_amt2 = account_position_amt(client, symbol, hedge)
                    if hedge:
                        if long_amt2 == 0 and short_amt2 == 0:
                            cancel_all_open(client, symbol, self.log)
                    else:
                        if long_amt2 == 0:
                            cancel_all_open(client, symbol, self.log)

                except Exception:
                    self.log("Loop error:\n" + traceback.format_exc())
                time.sleep(poll)

        except Exception:
            import traceback as _tb
            self.log("FATAL run error:\n" + _tb.format_exc())


# ================================ Tooltip (واحد فقط) =================
class Tooltip:
    def __init__(self, widget, text: str, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _):
        self.after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x, y, cx, cy = self.widget.bbox("insert")
        except Exception:
            x = y = cx = cy = 0
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Segoe UI", 9)
        )
        lbl.pack(ipadx=6, ipady=4)

    def _hide(self, _=None):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# ================================ GUI ================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Binance Futures Bot — EMA/RSI + Hedge + OCO")
        self.geometry("1040x740")
        self.minsize(980, 680)

        self.queue = queue.Queue()
        self.worker: Optional[BotWorker] = None
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # وضع ليلي
        self.var_dark = tk.BooleanVar(value=True)

        self._build()
        self.after(0, lambda: self._apply_theme(self.var_dark.get()))
        self._poll_queue()

    # ====================== بناء الواجهة ======================
    def _build(self):
        pad = {"padx": 6, "pady": 6}

        # شريط علوي سريع: تبديل الوضع الليلي
        topbar = ttk.Frame(self); topbar.pack(fill="x", **pad)
        ttk.Checkbutton(
            topbar, text="DARK MODE", variable=self.var_dark,
            command=lambda: self._apply_theme(self.var_dark.get())
        ).pack(side="right")

        # Config frame
        cfg = ttk.LabelFrame(self, text="الإعدادات")
        cfg.pack(fill="x", **pad)

        # ======= Vars =======
        self.var_symbol = tk.StringVar(value="BTCUSDT")
        self.var_interval = tk.StringVar(value="1m")
        self.var_lookback = tk.StringVar(value="400")
        self.var_testnet = tk.BooleanVar(value=True)
        self.var_dryrun = tk.BooleanVar(value=True)
        self.var_hedge = tk.BooleanVar(value=False)
        self.var_leverage = tk.StringVar(value="10")
        self.var_margin = tk.StringVar(value="ISOLATED")
        self.var_pos_usdt = tk.StringVar(value="50")
        self.var_rsi_long = tk.StringVar(value="50")
        self.var_rsi_short = tk.StringVar(value="50")
        self.var_atr_mult = tk.StringVar(value="2.0")
        self.var_rr = tk.StringVar(value="1.5")
        self.var_poll = tk.StringVar(value="10")
        self.var_working_type = tk.StringVar(value="MARK_PRICE")
        self.var_strategy = tk.StringVar(value="EMA_RSI")
        self.var_breakout_lb = tk.StringVar(value="20")
        self.var_mr_low = tk.StringVar(value="30")
        self.var_mr_high = tk.StringVar(value="70")

        # ======= Rows =======
        r1 = ttk.Frame(cfg); r1.pack(fill="x")
        ttk.Label(r1, text="Symbol").grid(row=0, column=0, sticky="w"); e_sym = ttk.Entry(r1, textvariable=self.var_symbol, width=10); e_sym.grid(row=0, column=1)
        ttk.Label(r1, text="Interval").grid(row=0, column=2, sticky="w"); e_int = ttk.Entry(r1, textvariable=self.var_interval, width=6); e_int.grid(row=0, column=3)
        ttk.Label(r1, text="Lookback").grid(row=0, column=4, sticky="w"); e_lb = ttk.Entry(r1, textvariable=self.var_lookback, width=6); e_lb.grid(row=0, column=5)
        c_test = ttk.Checkbutton(r1, text="TESTNET", variable=self.var_testnet); c_test.grid(row=0, column=6, sticky="w")
        c_dry  = ttk.Checkbutton(r1, text="DRY-RUN", variable=self.var_dryrun); c_dry.grid(row=0, column=7, sticky="w")
        c_hed  = ttk.Checkbutton(r1, text="HEDGE", variable=self.var_hedge);   c_hed.grid(row=0, column=8, sticky="w")

        r2 = ttk.Frame(cfg); r2.pack(fill="x")
        ttk.Label(r2, text="Leverage").grid(row=0, column=0, sticky="w"); e_lev = ttk.Entry(r2, textvariable=self.var_leverage, width=6); e_lev.grid(row=0, column=1)
        ttk.Label(r2, text="Margin").grid(row=0, column=2, sticky="w"); cb_mg = ttk.Combobox(r2, textvariable=self.var_margin, values=["ISOLATED","CROSSED"], width=10, state="readonly"); cb_mg.grid(row=0, column=3)
        ttk.Label(r2, text="Position $USDT").grid(row=0, column=4, sticky="w"); e_pos = ttk.Entry(r2, textvariable=self.var_pos_usdt, width=8); e_pos.grid(row=0, column=5)
        ttk.Label(r2, text="Poll (s)").grid(row=0, column=6, sticky="w"); e_poll = ttk.Entry(r2, textvariable=self.var_poll, width=6); e_poll.grid(row=0, column=7)
        ttk.Label(r2, text="Trigger on").grid(row=0, column=8, sticky="w"); cb_trg = ttk.Combobox(r2, textvariable=self.var_working_type, values=["MARK_PRICE","CONTRACT_PRICE"], width=14, state="readonly"); cb_trg.grid(row=0, column=9)

        r3 = ttk.Frame(cfg); r3.pack(fill="x")
        ttk.Label(r3, text="Strategy").grid(row=0, column=0, sticky="w"); cb_st = ttk.Combobox(r3, textvariable=self.var_strategy, values=["EMA_RSI","BREAKOUT","MEANREV","GRID"], width=12, state="readonly"); cb_st.grid(row=0, column=1)
        ttk.Label(r3, text="RSI Long>").grid(row=0, column=2, sticky="w"); e_rl = ttk.Entry(r3, textvariable=self.var_rsi_long, width=6); e_rl.grid(row=0, column=3)
        ttk.Label(r3, text="RSI Short<").grid(row=0, column=4, sticky="w"); e_rs = ttk.Entry(r3, textvariable=self.var_rsi_short, width=6); e_rs.grid(row=0, column=5)
        ttk.Label(r3, text="ATR x SL").grid(row=0, column=6, sticky="w"); e_atr = ttk.Entry(r3, textvariable=self.var_atr_mult, width=6); e_atr.grid(row=0, column=7)
        ttk.Label(r3, text="RR (TP)").grid(row=0, column=8, sticky="w"); e_rr = ttk.Entry(r3, textvariable=self.var_rr, width=6); e_rr.grid(row=0, column=9)

        r4 = ttk.Frame(cfg); r4.pack(fill="x")
        ttk.Label(r4, text="Breakout LB").grid(row=0, column=0, sticky="w"); e_blb = ttk.Entry(r4, textvariable=self.var_breakout_lb, width=6); e_blb.grid(row=0, column=1)
        ttk.Label(r4, text="MR RSI Low").grid(row=0, column=2, sticky="w"); e_mrl = ttk.Entry(r4, textvariable=self.var_mr_low, width=6); e_mrl.grid(row=0, column=3)
        ttk.Label(r4, text="MR RSI High").grid(row=0, column=4, sticky="w"); e_mrh = ttk.Entry(r4, textvariable=self.var_mr_high, width=6); e_mrh.grid(row=0, column=5)

        # === تلميحات (Tooltips) ===
        tips = {
            e_sym: "الرمز (مثال: BTCUSDT).",
            e_int: "الإطار الزمني للشموع (1m/5m/15m/1h...).",
            e_lb:  "عدد الشموع التي نجلبها للحسابات.",
            c_test: "التشغيل على TESTNET للتجربة.",
            c_dry:  "تشغيل تجريبي بدون إرسال أوامر حقيقية.",
            c_hed:  "Hedge Mode: فتح LONG و SHORT لنفس الرمز.",
            e_lev:  "الرافعة المالية (مثال 10x).",
            cb_mg:  "نوع المارجن: ISOLATED أو CROSSED.",
            e_pos:  "قيمة الصفقة بالدولار لكل دخول.",
            e_poll: "فاصل التحديث بالثواني.",
            cb_trg: "نوع السعر المستخدم لتفعيل أوامر الوقف/الهدف.",
            cb_st:  "الاستراتيجية المستخدمة.",
            e_rl:   "شرط RSI للدخول LONG.",
            e_rs:   "شرط RSI للدخول SHORT.",
            e_atr:  "مضاعف ATR لمسافة وقف الخسارة.",
            e_rr:   "نسبة العائد إلى المخاطرة لتحديد الهدف.",
            e_blb:  "عدد الشموع لحساب أعلى/أدنى (Breakout).",
            e_mrl:  "RSI منخفض للدخول العكسي (Mean Reversion).",
            e_mrh:  "RSI مرتفع للدخول العكسي (Mean Reversion).",
        }
        for w, t in tips.items():
            Tooltip(w, t)

        # Controls
        ctr = ttk.Frame(self); ctr.pack(fill="x", **pad)
        self.btn_start = ttk.Button(ctr, text="ابدأ التشغيل", command=self.on_start)
        self.btn_stop = ttk.Button(ctr, text="إيقاف", command=self.on_stop, state="disabled")
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop.pack(side="left", padx=4)

        # Status panel
        stat = ttk.LabelFrame(self, text="الحالة الحالية")
        stat.pack(fill="x", **pad)
        self.lbl_mark = ttk.Label(stat, text="Mark: -"); self.lbl_mark.pack(side="left", padx=10)
        self.lbl_rsi  = ttk.Label(stat, text="RSI: -");  self.lbl_rsi.pack(side="left", padx=10)
        self.lbl_ema  = ttk.Label(stat, text="EMA20/50: -/-"); self.lbl_ema.pack(side="left", padx=10)
        self.lbl_atr  = ttk.Label(stat, text="ATR: -");  self.lbl_atr.pack(side="left", padx=10)
        self.lbl_qty  = ttk.Label(stat, text="Qty: -");  self.lbl_qty.pack(side="left", padx=10)
        self.lbl_pos  = ttk.Label(stat, text="Position: 0"); self.lbl_pos.pack(side="left", padx=10)
        self.lbl_sig  = ttk.Label(stat, text="Signal: …"); self.lbl_sig.pack(side="left", padx=10)
        self.lbl_str  = ttk.Label(stat, text="Strategy: -"); self.lbl_str.pack(side="left", padx=10)

        # Log pane
        logf = ttk.LabelFrame(self, text="اللوج / تفاصيل التنفيذ")
        logf.pack(fill="both", expand=True, padx=6, pady=6)

        # صندوق نص قابل للتمرير والنسخ
        self.txt = scrolledtext.ScrolledText(
            logf, height=14, wrap="none", font=("Consolas", 10)
        )
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

        # منيو يمين-كليك: نسخ/تحديد الكل/مسح
        self._make_log_menu()
        self.txt.bind("<Button-3>", self._on_log_right_click)  # Right click

        # تهيئة ألوان Tags للّوج (اختياري تستخدمها)
        self.txt.tag_configure("ok",   foreground="#0a7f2e")
        self.txt.tag_configure("warn", foreground="#c77d00")
        self.txt.tag_configure("err",  foreground="#b00020")
        self.txt.tag_configure("info", foreground="#0b5fa5")

    # ====================== الثيم الداكن/الفاتح ======================
    def _apply_theme(self, dark: bool):
        if dark:
            bg   = "#0f1318"
            sbg  = "#141a21"
            fg   = "#e6edf3"
            dim  = "#A0ACB8"
            acc  = "#2F81F7"
            box  = "#222A33"
        else:
            bg   = "#F5F7FA"
            sbg  = "#FFFFFF"
            fg   = "#111827"
            dim  = "#4b5563"
            acc  = "#2563eb"
            box  = "#E5E7EB"

        self.configure(bg=bg)

        for sty in ("TFrame","TLabelframe","TLabelframe.Label","TLabel"):
            self.style.configure(sty, background=bg if sty in ("TFrame","TLabelframe") else bg, foreground=fg)
        self.style.configure("Card.TLabelframe", background=sbg, bordercolor=box, relief="solid")
        self.style.configure("TEntry", fieldbackground=sbg, background=sbg, foreground=fg)
        self.style.map("TEntry", fieldbackground=[("disabled", sbg)], foreground=[("disabled", dim)])
        self.style.configure("TCombobox", fieldbackground=sbg, background=sbg, foreground=fg, arrowcolor=fg)
        self.style.configure("TCheckbutton", background=bg, foreground=fg)
        self.style.configure("TButton", background=acc, foreground="#ffffff", padding=6)
        self.style.map("TButton", background=[("active", acc)], foreground=[("active", "#ffffff")])

        for lf in self.winfo_children():
            if isinstance(lf, ttk.Labelframe):
                lf.configure(style="Card.TLabelframe")

        try:
            self.txt.configure(
                background=sbg, foreground=fg, insertbackground=fg,
                selectbackground=acc, selectforeground="#ffffff",
                borderwidth=1, highlightthickness=1, highlightbackground=box
            )
            vs = self.txt.vbar if hasattr(self.txt, "vbar") else None
            hs = getattr(self.txt, "hbar", None)
            for sb in (vs, hs):
                if sb:
                    sb.configure(background=sbg, troughcolor=bg, highlightthickness=0, bd=0)
        except Exception:
            pass

    # ====================== منيو اللوج ======================
    def _on_log_right_click(self, event):
        try:
            self._log_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu.grab_release()

    def _make_log_menu(self):
        self._log_menu = tk.Menu(self, tearoff=0)
        self._log_menu.add_command(label="نسخ", command=self._copy_log_selection)
        self._log_menu.add_command(label="تحديد الكل", command=self._select_log_all)
        self._log_menu.add_separator()
        self._log_menu.add_command(label="مسح", command=self._clear_log)

    def _copy_log_selection(self):
        try:
            sel = self.txt.get("sel.first", "sel.last")
        except tk.TclError:
            sel = ""
        if not sel:
            sel = self.txt.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(sel)

    def _select_log_all(self):
        self.txt.tag_add("sel", "1.0", "end-1c")

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # ====================== إعدادات + تشغيل ======================
    def cfg_dict(self):
        return {
            "symbol": self.var_symbol.get().strip().upper(),
            "interval": self.var_interval.get().strip(),
            "lookback": int(self.var_lookback.get()),
            "testnet": bool(self.var_testnet.get()),
            "dry_run": bool(self.var_dryrun.get()),
            "hedge_mode": bool(self.var_hedge.get()),
            "leverage": int(self.var_leverage.get()),
            "margin_type": self.var_margin.get(),
            "position_usdt": Decimal(self.var_pos_usdt.get()),
            "rsi_long": float(self.var_rsi_long.get()),
            "rsi_short": float(self.var_rsi_short.get()),
            "atr_mult": Decimal(self.var_atr_mult.get()),
            "rr": Decimal(self.var_rr.get()),
            "poll_seconds": float(self.var_poll.get()),
            "working_type": self.var_working_type.get(),
            "strategy": self.var_strategy.get(),
            "breakout_lookback": int(self.var_breakout_lb.get()),
            "mr_rsi_low": float(self.var_mr_low.get()),
            "mr_rsi_high": float(self.var_mr_high.get()),
        }

    def on_start(self):
        try:
            cfg = self.cfg_dict()
        except Exception as e:
            messagebox.showerror("Config Error", f"خطأ في الإعدادات: {e}")
            return
        self.worker = BotWorker(cfg, self.queue)
        self.worker.start()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._append_log("بدأ التشغيل…", tag="info")

    def on_stop(self):
        if self.worker:
            self.worker.stop()
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._append_log("تم الإيقاف.", tag="warn")

    # لوج مع طابع زمني + تلوين اختياري
    def _append_log(self, msg: str, tag: str | None = None):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.txt.configure(state="normal")
        if tag:
            self.txt.insert("end", line, tag)
        else:
            self.txt.insert("end", line)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status":
                    d = payload
                    self.lbl_mark.config(text=f"Mark: {d['mark']}")
                    self.lbl_rsi.config(text=f"RSI: {d['rsi']}")
                    self.lbl_ema.config(text=f"EMA20/50: {d['ema20']}/{d['ema50']}")
                    self.lbl_atr.config(text=f"ATR: {d['atr']}")
                    self.lbl_qty.config(text=f"Qty: {d['qty']}")
                    self.lbl_pos.config(text=f"Position: {d['pos']}")
                    sig = "LONG" if d.get("long_sig") else ("SHORT" if d.get("short_sig") else "—")
                    self.lbl_sig.config(text=f"Signal: {sig}")
                    self.lbl_str.config(text=f"Strategy: {d.get('strategy','-')}")
        except queue.Empty:
            pass
        self.after(300, self._poll_queue)


if __name__ == "__main__":
    app = App()
    app.mainloop()
