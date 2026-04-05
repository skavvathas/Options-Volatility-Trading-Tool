import threading
import time
import re
import tkinter as tk
from tkinter import ttk, messagebox
from turtle import width
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta, timezone
import warnings
warnings.filterwarnings('ignore')

from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import ContractType


def _parse_osi_option_symbol(sym: str):
    """
    Turn an Alpaca option ticker string into expiry, call/put, and strike.

    US equity options use a fixed tail format (OCC / OSI style), for example:
        AAPL240628C00200000
        |    |     | |
        root YYMMDD C/P strike encoded as 8 digits (strike dollars * 1000)

    We only need the last 15 characters: 6 date + 1 C or P + 8 strike digits.
    Everything before that is the underlying root (length varies: AAPL, SPY, etc.).
    """
    sym = sym.strip().upper()
    if len(sym) < 16:
        return None
    # Last 15 chars are always YYMMDD + C|P + 8-digit strike
    tail = sym[-15:]
    root = sym[:-15]
    yymmdd, cp, strike_s = tail[:6], tail[6], tail[7:]
    if cp not in ("C", "P") or not yymmdd.isdigit() or not strike_s.isdigit():
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    try:
        exp = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    # Strike is stored in thousandths of a dollar (200.00 -> 00200000)
    strike = int(strike_s) / 1000.0
    return {"root": root, "expiration": exp, "right": cp, "strike": strike}


class AlpacaApp:

    def __init__(self, root, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        
        # Stock bars: OHLCV only (no implied vol).
        self.client = StockHistoricalDataClient(api_key, secret_key)
        # Options endpoints: snapshots / chain include implied_volatility and greeks.
        self.option_client = OptionHistoricalDataClient(api_key, secret_key)

        self.connected = False
        self.market_data = {}
        self.historical_data = {}

    def connect(self):
        try:
            # Alpaca doesn't require persistent socket connection for REST
            self.connected = True
            print("Connected to Alpaca API")
        except Exception as e:
            print(f"Connection error: {e}")

    def get_historical_data(self, symbol, timeframe=TimeFrame.Day, days=30):
        """
        Download historical *stock* bars for `symbol` (not options).

        Returns a pandas DataFrame with at least OHLCV; there is no IV on stocks.
        Alpaca returns one row per bar; for a single ticker we peel off the
        (symbol, timestamp) MultiIndex so you get a flat table.
        """
        symbol = str(symbol).strip().upper()
        if not symbol:
            return None
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                feed="iex"
            )

            bars = self.client.get_stock_bars(request)
            df = bars.df

            if df is None or df.empty:
                return df

            # MultiIndex level 0 = symbol, level 1 = bar time — keep only our symbol
            if isinstance(df.index, pd.MultiIndex):
                if symbol not in df.index.get_level_values(0):
                    return None
                df = df.xs(symbol, level=0)

            df = df.reset_index(drop=False)

            self.historical_data[symbol] = df
            return df

        except Exception as e:
            print(f"Error fetching historical data: {e}")
            return None

    def get_near_atm_call_iv(self, underlying, spot, strike_target, dte_target):
        """
        Fetch one implied-volatility number from Alpaca *options* data.

        Important:
        - `get_stock_bars` / stock data never includes IV.
        - Option *bars* are still just OHLCV; IV lives on option *snapshots* / chain.
        Here we call `get_option_chain` (snapshots for all matching contracts),
        then pick the call whose expiry and strike are closest to what you asked for.

        Args:
            underlying: Stock ticker, e.g. "AAPL".
            spot: Last stock price (used to widen strike search if needed).
            strike_target: Prefer IV near this strike (often = spot or your strike field).
            dte_target: Prefer expiries about this many calendar days out (from UI).
        """
        underlying = str(underlying).strip().upper()
        today = date.today()
        # Window around the user's "days to expiry" so we still find expiries after weekends.
        exp_start = today + timedelta(days=max(1, dte_target - 14))
        exp_end = today + timedelta(days=dte_target + 14)

        # Try a tight strike band first (less data), then wider, then drop strike filter entirely.
        strike_bands = [
            (
                min(strike_target, spot) * 0.92,
                max(strike_target, spot) * 1.08,
            ),
            (spot * 0.85, spot * 1.15),
        ]

        for strike_lo, strike_hi in strike_bands:
            req = OptionChainRequest(
                underlying_symbol=underlying,
                type=ContractType.CALL,
                strike_price_gte=float(strike_lo),
                strike_price_lte=float(strike_hi),
                expiration_date_gte=exp_start.isoformat(),
                expiration_date_lte=exp_end.isoformat(),
            )
            chain = self.option_client.get_option_chain(req)
            iv = self._pick_chain_iv(chain, spot, strike_target, dte_target, today)
            if iv is not None:
                return iv

        # Last resort: same expiry window but all strikes in range (heavier response).
        req = OptionChainRequest(
            underlying_symbol=underlying,
            type=ContractType.CALL,
            expiration_date_gte=exp_start.isoformat(),
            expiration_date_lte=exp_end.isoformat(),
        )
        chain = self.option_client.get_option_chain(req)
        return self._pick_chain_iv(chain, spot, strike_target, dte_target, today)

    def _pick_chain_iv(self, chain, spot, strike_target, dte_target, today):
        """
        From a dict of option_symbol -> snapshot, choose the "best" call's IV.

        `chain` comes from `get_option_chain`: each value has `.implied_volatility`.
        We score each contract by how far its DTE and strike are from the targets
        (lower score = better match). Returns IV as a float (usually 0–1, e.g. 0.32 = 32%).
        """
        if not chain:
            return None
        best_iv = None
        best_score = None
        for occ_sym, snap in chain.items():
            if snap.implied_volatility is None:
                continue
            meta = _parse_osi_option_symbol(occ_sym)
            if not meta or meta["right"] != "C":
                continue
            dte = (meta["expiration"] - today).days
            # Mix calendar DTE error and normalized strike error into one score
            score = abs(dte - dte_target) + abs(meta["strike"] - strike_target) / max(
                spot, 1e-6
            )
            if best_score is None or score < best_score:
                best_score = score
                best_iv = snap.implied_volatility
        return best_iv


class VolatilityCrushAnalyzer:

    def __init__(self, root, api_key, secret_key):
        self.root = root
        self.root.title("Volatility Crush Analyzer")
        self.root.geometry("1000x700")

        self.api_key = api_key
        self.secret_key = secret_key

        # Alpaca API
        self.alpaca_app = AlpacaApp(root, api_key, secret_key)
        self.connected = False

        self.current_spot = None
        self.current_iv = None
        self.ticker = None

        self.risk_free_rate = 0.05
        self.setup_ui()


    def create_equity_contract(self, symbol):
        symbolOfEquity = symbol.upper()

        return symbolOfEquity


    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)


        title_label = ttk.Label(main_frame, text="Volatility Crush Analyzer", 
                            font=("Helvetica", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))

        left_frame = ttk.Frame(main_frame)
        left_frame.grid(row=1, column=0, sticky=(tk.W, tk.N, tk.S), padx=(0, 10))
        left_frame.columnconfigure(0, weight=1)

        right_frame = ttk.Frame(main_frame)
        right_frame.grid(row=1, column=1, sticky=(tk.E, tk.N, tk.S), padx=(0, 10))
        right_frame.columnconfigure(0, weight=1)

        # Left Frame
        self.setup_connection_section(left_frame, 0)
        self.setup_market_data_section(left_frame, 1)
        self.setup_current_straddle_section(left_frame, 2)
        self.setup_current_greeks_section(left_frame, 3)

        # Right Frame
        self.setup_scenario_section(right_frame, 0)
        self.setup_pnl_section(right_frame, 1)
        self.setup_new_greeks_section(right_frame, 2)
        self.setup_status_section(right_frame, 3)


    def setup_connection_section(self, parent, row):
        connection_frame = ttk.LabelFrame(parent, text="AlpacaConnection", padding="10")
        connection_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        connection_frame.columnconfigure(1, weight=1)
        connection_frame.columnconfigure(3, weight=1)

        '''
        ttk.Label(connection_frame, text="Host:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.host_var = tk.StringVar(value='127.0.0.1')
        ttk.Entry(connection_frame, textvariable=self.host_var, width=20).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 15))

        ttk.Label(connection_frame, text="Port:").grid(row=0, column=2, sticky=tk.W, pady=(0, 5))
        self.port_var = tk.StringVar(value='7497')
        ttk.Entry(connection_frame, textvariable=self.port_var, width=10).grid(row=0, column=3, sticky=(tk.W, tk.E), pady=(0, 15))
        '''

        button_frame = ttk.Frame(connection_frame)
        button_frame.grid(row=1, column=0, columnspan=4, pady=(0, 10))


        self.connect_btn = ttk.Button(button_frame, text="Connect to Alpaca", command=self.connect_alpaca)
        self.connect_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.disconnect_btn = ttk.Button(button_frame, text="Disconnect from Alpaca", command=self.disconnect_alpaca)
        self.disconnect_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.status_label = ttk.Label(connection_frame, text="Disconnected", font=("Helvetica", 12), foreground="red")
        self.status_label.grid(row=2, column=0, columnspan=4, pady=(10, 0))


    def setup_market_data_section(self, parent, row):
        market_data_frame = ttk.LabelFrame(parent, text="Market Data", padding="10")
        market_data_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        market_data_frame.columnconfigure(1, weight=1)

        ttk.Label(market_data_frame, text="Ticker:").grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        ticker_frame = ttk.Frame(market_data_frame)
        ticker_frame.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 8))
        ticker_frame.columnconfigure(0, weight=1)

        self.ticker_var = tk.StringVar(value="NBIS")
        ttk.Entry(ticker_frame, textvariable=self.ticker_var, width=12, font=("Helvetica", 12)).grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 8))


        # Fetch Market Data
        self.fetch_btn = ttk.Button(ticker_frame, text="Fetch Market Data", command=self.fetch_market_data)
        self.fetch_btn.grid(row=2, column=0, sticky=tk.W, pady=(0, 8))


        # spot price, strike price, iv, days to expiry
        ttk.Label(market_data_frame, text='Spot Price:').grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.spot_price_var = tk.StringVar()
        ttk.Entry(market_data_frame, textvariable=self.spot_price_var, width=6, font=("Helvetica", 12)).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # strike price
        ttk.Label(market_data_frame, text='Strike Price:').grid(row=1, column=2, sticky=tk.W, pady=(0, 8))
        self.strike_price_var = tk.StringVar()
        ttk.Entry(market_data_frame, textvariable=self.strike_price_var, width=6, font=("Helvetica", 12)).grid(row=1, column=3, sticky=(tk.W, tk.E), pady=(0, 8))


        # iv
        ttk.Label(market_data_frame, text='IV (%):').grid(row=3, column=0, sticky=tk.W, pady=(0, 8))
        self.iv_var = tk.StringVar()
        ttk.Entry(market_data_frame, textvariable=self.iv_var, width=6, font=("Helvetica", 12)).grid(row=3, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # days to expiry
        ttk.Label(market_data_frame, text='Days to expiry:').grid(row=3, column=2, sticky=tk.W, pady=(0, 8))
        self.days_var = tk.StringVar(value="30")
        ttk.Entry(market_data_frame, textvariable=self.days_var, width=6, font=("Helvetica", 12)).grid(row=3, column=3, sticky=(tk.W, tk.E), pady=(0, 8))

        # Price Straddle Button
        self.price_btn = ttk.Button(market_data_frame, text="Price Straddle", command=self.price_current_straddle)
        self.price_btn.grid(row=5, column=0, columnspan=2, padx=(10, 0))


    def setup_current_straddle_section(self, parent, row):
        pricing_frame = ttk.LabelFrame(parent, text="Current Straddle Price", padding="10")
        pricing_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        pricing_frame.columnconfigure(1, weight=1)

        # Label for call price
        ttk.Label(pricing_frame, text='Call Price:').grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.call_price_label = ttk.Label(pricing_frame, text="$0.00", font=("Helvetica", 12, "bold"), foreground="green")
        self.call_price_label.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # Label for put price
        ttk.Label(pricing_frame, text='Put Price:').grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.put_price_label = ttk.Label(pricing_frame, text="$0.00", font=("Helvetica", 12, "bold"), foreground="red")
        self.put_price_label.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # Separator
        separator = ttk.Separator(pricing_frame, orient='horizontal')
        separator.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))

        # Label for straddle price
        ttk.Label(pricing_frame, text='Straddle Price:').grid(row=3, column=0, sticky=tk.W, pady=(0, 8))
        self.straddle_price_label = ttk.Label(pricing_frame, text="$0.00", font=("Helvetica", 15, "bold"), foreground="blue")
        self.straddle_price_label.grid(row=3, column=1, sticky=(tk.W, tk.E), pady=(0, 8))


    def setup_current_greeks_section(self, parent, row):
        greeks_frame = ttk.LabelFrame(parent, text="Current Greeks", padding="10")
        greeks_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        greeks_frame.columnconfigure(1, weight=1)
        greeks_frame.columnconfigure(3, weight=1)

        # Label for delta
        ttk.Label(greeks_frame, text='Delta:').grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.delta_label = ttk.Label(greeks_frame, text="0.00", font=("Helvetica", 10, "bold"))
        self.delta_label.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 5))

        # Label for gamma
        ttk.Label(greeks_frame, text='Gamma:').grid(row=0, column=2, sticky=tk.W, pady=(0, 8))
        self.gamma_label = ttk.Label(greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.gamma_label.grid(row=0, column=3, sticky=(tk.W, tk.E), pady=(0, 5))

        #Label for vega
        ttk.Label(greeks_frame, text='Vega:').grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.vega_label = ttk.Label(greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.vega_label.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=(0, 5))

        # Label for theta
        ttk.Label(greeks_frame, text='Theta:').grid(row=1, column=2, sticky=tk.W, pady=(0, 8))
        self.theta_label = ttk.Label(greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.theta_label.grid(row=1, column=3, sticky=(tk.W, tk.E), pady=(0, 5))


    def setup_scenario_section(self, parent, row):
        scenario_frame = ttk.LabelFrame(parent, text="Scenarion Analysis", padding="10")
        scenario_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        scenario_frame.columnconfigure(1, weight=1)

        # new spot price
        ttk.Label(scenario_frame, text='New Spot Price:').grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.new_spot_price_var = tk.StringVar()
        ttk.Entry(scenario_frame, textvariable=self.new_spot_price_var, width=12, font=("Helvetica", 12)).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # new IV
        # make sure here that you know the IV units
        ttk.Label(scenario_frame, text='New IV (%):').grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.new_iv_var = tk.StringVar()
        ttk.Entry(scenario_frame, textvariable=self.new_iv_var, width=12, font=("Helvetica", 12)).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        self.analyze_btn = ttk.Button(scenario_frame, text="Analyze Scenario", command=self.analyze_scenario, state='disabled')
        self.analyze_btn.grid(row=2, column=0, columnspan=2, padx=(10, 0))


    def setup_pnl_section(self, parent, row):
        pnl_frame = ttk.LabelFrame(parent, text="P&L Analysis", padding="10")
        pnl_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        pnl_frame.columnconfigure(1, weight=1)

        # New straddle price
        ttk.Label(pnl_frame, text='New Straddle Price:').grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.new_straddle_price_label = ttk.Label(pnl_frame, text="$0.00", font=("Helvetica", 15, "bold"))
        self.new_straddle_price_label.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # separator
        separator = ttk.Separator(pnl_frame, orient='horizontal')
        separator.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))

        # Long Straddle P&L
        ttk.Label(pnl_frame, text='Long Straddle P&L:').grid(row=2, column=0, sticky=tk.W, pady=(0, 8))
        self.pnl_long_label = ttk.Label(pnl_frame, text="$0.00", font=("Helvetica", 15, "bold"))
        self.pnl_long_label.grid(row=2, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # Short Straddle P&L
        ttk.Label(pnl_frame, text='Short Straddle P&L:').grid(row=3, column=0, sticky=tk.W, pady=(0, 8))
        self.pnl_short_label = ttk.Label(pnl_frame, text="$0.00", font=("Helvetica", 15, "bold"))
        self.pnl_short_label.grid(row=3, column=1, sticky=(tk.W, tk.E))


    def setup_new_greeks_section(self, parent, row):
        new_greeks_frame = ttk.LabelFrame(parent, text="New Greeks", padding="10")
        new_greeks_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        new_greeks_frame.columnconfigure(1, weight=1)
        new_greeks_frame.columnconfigure(3, weight=1)


        # New delta
        ttk.Label(new_greeks_frame, text='New Delta:').grid(row=0, column=0, sticky=tk.W, pady=(0, 8))
        self.new_delta_label = ttk.Label(new_greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.new_delta_label.grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 8))

        # New gamma
        ttk.Label(new_greeks_frame, text='New Gamma:').grid(row=0, column=2, sticky=tk.W, pady=(0, 8))
        self.new_gamma_label = ttk.Label(new_greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.new_gamma_label.grid(row=0, column=3, sticky=(tk.W, tk.E), pady=(0, 8))

        # New vega
        ttk.Label(new_greeks_frame, text='New Vega:').grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        self.new_vega_label = ttk.Label(new_greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.new_vega_label.grid(row=1, column=1, sticky=(tk.W, tk.E), pady=(0, 8))
        
        # New theta
        ttk.Label(new_greeks_frame, text='New Theta:').grid(row=1, column=2, sticky=tk.W, pady=(0, 8))
        self.new_theta_label = ttk.Label(new_greeks_frame, text="0.00", font=("Helvetica", 12, "bold"))
        self.new_theta_label.grid(row=1, column=3, sticky=(tk.W, tk.E), pady=(0, 8))


    def setup_status_section(self, parent, row):
        status_frame = ttk.LabelFrame(parent, text="Status", padding="10")
        status_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        status_frame.columnconfigure(1, weight=1)
        status_frame.rowconfigure(0, weight=1)

        # status label
        self.status_var = tk.StringVar(value="Ready to connect to Alpaca...")
        self.status_display = ttk.Label(status_frame, textvariable=self.status_var, wraplength=300, font=("Helvetica", 12, "bold"))
        self.status_display.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 8))


    def update_status(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"[{timestamp}] {message}")
        self.root.update_idletasks()


    # Connect Alpaca 
    def connect_alpaca(self):
        try:

            self.update_status(f"Connecting to Alpaca...")

            # Daily bars need enough calendar days to include at least one session
            # (weekends/holidays); days=1 often returns an empty frame and looks like failure.
            test_symbol = (self.ticker_var.get() or "").strip().upper() or "NBIS"

            df = self.alpaca_app.get_historical_data(test_symbol, days=7)

            if df is not None and not df.empty:
                self.connected = True
                self.alpaca_app.connected = True

                self.status_label.config(text="Connected", foreground="green")
                self.update_status(f"Connected to Alpaca. Data received for {test_symbol}")

                # Enable buttons
                self.disconnect_btn.config(state="normal")
                self.connect_btn.config(state="disabled")
                self.fetch_btn.config(state="normal")
                self.analyze_btn.config(state="normal")

            else:
                self.update_status(f"Connected but server is not available")
                raise Exception("No data returned from API")

        except Exception as e:
            self.connected = False
            self.alpaca_app.connected = False

            self.status_label.config(text="Disconnected", foreground="red")
            self.update_status(f"Connection failed: {e}")


    def price_current_straddle(self):
        print('current straddle')

    def analyze_scenario(self):
        print('analyze scenario')

    def disconnect_alpaca(self):
        try: 
            self.connected = False
            self.connect_btn.config(state='normal')
            self.disconnect_btn.config(state="disabled")
            self.fetch_btn.config(state="disabled")
            self.price_btn.config(state='disabled')
            self.analyze_btn.config(state="disabled")
            self.status_label.config(text='Disconnected', foreground='red')

            self.clear_data()
            self.update_status('Disconnected from Alpaca')
        except Exception as e:
            self.update_status(f'Disconnect error {e}')

    def clear_data(self):
        self.current_spot = None
        self.current_iv = None

        labels_to_reset = [
            self.call_price_label, self.put_price_label, self.straddle_price_label,
            self.delta_label, self.gamma_label, self.vega_label, self.theta_label,
            self.new_straddle_price_label, self.pnl_long_label, self.pnl_short_label,
            self.new_delta_label, self.new_gamma_label, self.new_vega_label, self.new_theta_label
        ]

        self.spot_price_var.set("")
        self.strike_price_var.set("")
        self.iv_var.set("")
        self.new_spot_price_var.set("")
        self.new_iv_var.set("")

        for label in labels_to_reset:
            if 'price' in str(label):
                label.config(text="$0.00", foreground='black')
            else:
                label.config(text='0.000' if 'delta' in str(label) or 'gamma' in str(label) else "0.00", foreground='black')

        if hasattr(self, 'alpaca_app') and self.alpaca_app:
            self.alpaca_app.historical_data.clear()


    def fetch_market_data(self):
        if not self.connected:
            messagebox.showerror("Error", "Not connected to Alpaca")
            return

        self.ticker = self.ticker_var.get().strip().upper()
        if not self.ticker:
            messagebox.showerror("Error", "Enter a ticker symbol")
            return

        self.update_status(f"Fetching historical data for {self.ticker}...")

        self.alpaca_app.market_data.clear()
        self.alpaca_app.historical_data.clear()

        try:
            df = self.alpaca_app.get_historical_data(
                self.ticker, timeframe=TimeFrame.Day, days=5
            )
            if df is None or df.empty:
                self.update_status(f"No bars returned for {self.ticker}")
                messagebox.showerror("Error", f"No market data for {self.ticker}")
                return

            if "close" not in df.columns:
                self.update_status("Unexpected response: no 'close' column")
                messagebox.showerror("Error", "Unexpected bar data format from Alpaca")
                return

            ts_col = "timestamp" if "timestamp" in df.columns else None
            if ts_col:
                df = df.sort_values(ts_col)
            last = df.iloc[-1]
            spot = float(last["close"])
            self.spot_price_var.set(f"{spot:.2f}")
            self.current_spot = spot
            self.alpaca_app.market_data[self.ticker] = df

            dte_target = self._dte_target_for_options()
            strike_target = self._strike_target_for_options(spot)
            try:
                iv = self.alpaca_app.get_near_atm_call_iv(
                    self.ticker, spot, strike_target, dte_target
                )
            except Exception as opt_exc:
                iv = None
                self.update_status(f"Options IV fetch failed: {opt_exc}")

            if iv is not None:
                self.current_iv = iv
                iv_pct = iv * 100.0 if 0 < iv < 3 else iv
                self.iv_var.set(f"{iv_pct:.2f}")
                self.update_status(
                    f"{self.ticker}: spot {spot:.2f}, ATM call IV ~{iv_pct:.2f}% "
                    f"(~{dte_target}d target)"
                )
            else:
                self.current_iv = None
                self.update_status(
                    f"Loaded spot {spot:.2f}; IV not found (options data / filters / subscription)."
                )

        except Exception as e:
            self.update_status(f"Fetch failed: {e}")
            messagebox.showerror("Error", str(e))

        self.root.after(3000, self.process_market_data)


    def process_market_data(self):
        print('I am inside Process market data')
        print('historical data', self.alpaca_app.historical_data)
        if 1 in self.alpaca_app.historical_data and len(self.alpaca_app.historical_data[1]) > 0:
            price_data = self.alpaca_app.historical_data[1]
            latest_bar = price_data[1]
            self.current_spot = latest_bar['close']
            self.update_status(f"Latest closing price: ${self.current_spot: .2f}")
        else: 
            self.update_status("No historical price data received")
            return

        if 2 in self.alpaca_app.historical_data and len(self.alpaca_app.historical_data[2]) > 0:
            iv_data = self.alpaca_app.historical_data[2]
            latest_iv = iv_data[-1]
            self.current_iv = latest_iv['close']
            self.update_status(f"Latest closing price: ${self.current_spot:.2f}")
        else:
            self.update_status("No historical price data received")
            return

        self.spot_price_var.set(f"{self.current_spot:.2f}")
        self.strike_var.set(f"{self.current_spot:.2f}")
        self.iv_var.set(f"{self.current_iv:.4f}")

        self.price_current_straddle()


    def price_current_straddle(self):
        try:
            spot_price = float(self.spot_price_var.get())
            strike_price = float(self.spot_price_var.get())
            iv_percent = float(self.iv_var.get())
            days_to_expiry = int(self.days_var.get())
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number for all parameters")
            return

        iv_decimal = iv_percent / 100
        T = days_to_expiry / 365
        r = self.risk_free_rate

        call_price = self.black_scholes_call(spot_price, strike_price, T, r, iv_decimal)
        put_price = self.black_scholes_put(spot_price, strike_price, T, r, iv_decimal)
        straddle_price = call_price + put_price

        delta = self.calculate_delta(spot_price, strike_price, T, r, iv_decimal, 'call') + self.calculate_delta(spot_price, strike_price, T, r, iv_decimal, 'put')
        gamma = self.calculate_gamma(spot_price, strike_price, T, r, iv_decimal)
        vega = self.calculate_vega(spot_price, strike_price, T, r, iv_decimal)*2
        theta = self.calculate_theta(spot_price, strike_price, T, r, iv_decimal, 'call') + self.calculate_delta(spot_price, strike_price, T, r, iv_decimal, 'put')


        self.call_price_label.config(text=f"${call_price:.2f}", foreground='green')
        self.put_price_label.congig(text=f"${put_price:.2f}", foreground='green')
        self.straddle_price_label.config(text=f"${straddle_price:.2f}", foreground='green')

        # delta - gamma - vega - theta 
        self.delta_label.config(text=f"{delta:.3f}")
        self.gamma_label.config(text=f"{gamma:.3f}")
        self.vega_label.config(text=f"{vega:.2f}")
        self.theta_label.config(text=f"{theta:.2f}")

        self.analyze_btn.config('normal')

        if not self.new_spot_price.get():
            self.new_spot_price.set(f"{spot_price:.2f}")
        if not self.new_iv_var.get():
            self.new_iv_var.set(f"{iv_percent}")

        self.update_status(f"Straddle priced: ${straddle_price}, Call: ${call_price:.2f} + Put: ${put_price:.2f}")

    def analyze_scenario(self):
        try:
            new_spot = float(self.new_spot_price.get())
            new_iv = float(self.new_iv_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid spot price or IV values")
            return

        

    def _dte_target_for_options(self):
        raw = (self.days_var.get() or "").strip()
        try:
            return max(1, int(raw))
        except ValueError:
            return 30

    def _strike_target_for_options(self, spot):
        raw = (self.strike_price_var.get() or "").strip()
        if not raw:
            return spot
        try:
            return float(raw)
        except ValueError:
            return spot



if __name__ == "__main__":
    import tkinter as tk

    root = tk.Tk()

    # Define your API keys
    api_key = "PKVXLBIDJUXQ6VEMWDH3MLPQ5C"
    secret_key = "DBhhsymoUy6V2UuC8BBUJxMNc8pMdUwZoSm6kxeVHVq7"

    app = VolatilityCrushAnalyzer(root, api_key, secret_key)
    root.mainloop()