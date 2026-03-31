import threading
import time
from tkinter import N
from turtle import width
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

class AlpacaApp:

    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        
        self.client = StockHistoricalDataClient(api_key, secret_key)
        
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
        try:
            end = datetime.utcnow()
            start = end - timedelta(days=days)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=timeframe,
                start=start,
                end=end
            )

            bars = self.client.get_stock_bars(request)

            df = bars.df

            # If multi-index (symbol, time), flatten it
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol)

            df = df.reset_index()

            self.historical_data[symbol] = df

            print(f"Historical data received for {symbol}")
            return df

        except Exception as e:
            print(f"Error fetching historical data: {e}")
            return None

class VolatilityCrushAnalyzer:

    def __init__(self, root):
        self.root = root
        self.root.title("Volatility Crush Analyzer")
        self.root.geometry("800x600")

        # Alpaca API
        self.alpaca_app = AlpacaApp(api_key, secret_key)
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

        self.setup_connection_section(left_frame, 0)
        self.setup_market_data_section(left_frame, 1)
        self.setup_current_straddle_section(left_frame, 2)
        self.setup_current_greeks_section(left_frame, 3)

        self.setup_scenario_section(right_frame, 0)
        self.setup_pnl_section(right_frame, 1)
        self.setup_new_greeks_section(right_frame, 2)
        self.setup_status_section(right_frame, 3)


    def setup_connection_section(self, parent, row):
        connection_frame = ttk.LabelFrame(parent, text="AlpacaConnection", padding="10")
        connection_frame.grid(row=row, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        connection_frame.columnconfigure(1, weight=1)
        connection_frame.columnconfigure(3, weight=1)

        ttk.Label(connection_frame, text="Host:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.host_var = tk.StringVar(value='127.0.0.1')
        ttk.Entry(connection_frame, textvariable=self.host_var, width=20).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=(0, 15))

        ttk.Label(connection_frame, text="Port:").grid(row=0, column=2, sticky=tk.W, pady=(0, 5))
        self.port_var = tk.StringVar(value='7497')
        ttk.Entry(connection_frame, textvariable=self.port_var, width=10).grid(row=0, column=3, sticky=(tk.W, tk.E), pady=(0, 15))

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

        self.ticker_var = tk.StringVar(value="AAPL")
        ttk.Entry(ticker_frame, textvariable=self.ticker_var, width=12, font=("Helvetica", 12)).grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 8))


        self.fetch_btn = ttkButton(ticker_frame, text="Fetch Market Data", command=self.fetch_market_data)
        self.fetch_btn.pack(side=tk.RIGHT, padx=(0, 10))

        # to do add implied vol - spot price 