import threading
import time
import tkinter as tk
from tkinter import ttk
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

    def __init__(self, root, api_key, secret_key):
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

        self.ticker_var = tk.StringVar(value="AAPL")
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


    def connect_alpaca(self):
        try:

            self.update_status(f"Connecting to Alpaca...")

            # Try a simple API request to verify connection
            test_symbol = self.ticker_var.get().upper() or "AAPL"

            df = self.alpaca_app.get_historical_data(test_symbol, days=1)

            print('Dataframe from alpaca', df)

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
        print('disconnect alpaca')

    def fetch_market_data(self):
        print('fetch market data')



if __name__ == "__main__":
    import tkinter as tk

    root = tk.Tk()

    # Define your API keys
    api_key = "PKVXLBIDJUXQ6VEMWDH3MLPQ5C"
    secret_key = "DBhhsymoUy6V2UuC8BBUJxMNc8pMdUwZoSm6kxeVHVq7"

    app = VolatilityCrushAnalyzer(root, api_key, secret_key)
    root.mainloop()