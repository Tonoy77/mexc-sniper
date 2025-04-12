import time
import logging
from datetime import datetime
from pymexc import spot

class MexcSnipeBot:
    def __init__(self, api_key, api_secret):
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        self.http = spot.HTTP(api_key=api_key, api_secret=api_secret)

    def get_balances(self):
        try:
            data = self.http.account_information().get("balances", [])
            bal = {b['asset']: float(b['free']) for b in data if float(b['free']) > 0}
            self.logger.info(f"Fetched balances: {bal}")
            return bal
        except Exception as e:
            self.logger.error(f"Error fetching balances: {e}")
            return {}

    def buy_token(self, symbol, quote_amount):
        try:
            order = self.http.new_order(
                symbol=symbol.upper(),
                side="BUY",
                order_type="MARKET",
                quote_order_qty=quote_amount
            )
            self.logger.info(f"Manual BUY placed: {order['orderId']}")
            return order
        except Exception as e:
            self.logger.error(f"Manual BUY failed: {e}")

    def sell_token(self, symbol, quantity):
        try:
            order = self.http.new_order(
                symbol=symbol.upper(),
                side="SELL",
                order_type="MARKET",
                quantity=quantity
            )
            self.logger.info(f"Manual SELL placed: {order['orderId']}")
            return order
        except Exception as e:
            self.logger.error(f"Manual SELL failed: {e}")

    def take_profit_trade(self, symbol, buy_amount, take_profit_pct):
        """Market-buy a listed token, then sell at TP% if reached."""
        symbol = symbol.upper()
        # 1) Buy
        self.logger.info(f"TP Trade: buying {buy_amount} USDT of {symbol}")
        buy_order = self.buy_token(symbol, buy_amount)
        if not buy_order:
            self.logger.error("Buy failed, aborting TP trade")
            return

        # Fetch fill details
        filled = self.http.query_order(symbol=symbol, order_id=buy_order['orderId'])
        qty = float(filled['executedQty'])
        cost = float(filled['cummulativeQuoteQty'])
        buy_price = cost / qty
        self.logger.info(f"Bought {qty:.6f} {symbol} at {buy_price:.6f} USDT")

        # 2) Compute TP
        target_price = buy_price * (1 + take_profit_pct / 100)
        self.logger.info(f"TP set at {take_profit_pct}% → {target_price:.6f} USDT")

        # 3) Monitor at 10Hz
        while True:
            try:
                ticker = self.http.ticker_price(symbol=symbol)
                current = float(ticker['price'])
                self.logger.debug(f"Current price: {current:.6f}")
                if current >= target_price:
                    self.logger.info(f"TP hit ({current:.6f} ≥ {target_price:.6f}), selling {qty}")
                    sell_order = self.sell_token(symbol, qty)
                    if sell_order:
                        time.sleep(0.5)
                        status = self.http.query_order(symbol=symbol, order_id=sell_order['orderId'])
                        sell_price = float(status['cummulativeQuoteQty']) / float(status['executedQty'])
                        profit = (sell_price - buy_price) * qty
                        profit_pct = (sell_price / buy_price - 1) * 100
                        self.logger.info(
                            f"Sold at {sell_price:.6f}, profit {profit:.6f} USDT ({profit_pct:.2f}%)"
                        )
                    break
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Error in TP monitoring: {e}")
                break

    def snipe_listing(self, symbol, buy_amount, take_profit_pct, target_time_str):
        symbol = symbol.upper()
        target_time = datetime.strptime(target_time_str, "%Y-%m-%d %H:%M:%S")
        self.logger.info(f"Snipe scheduled for {symbol} at {target_time} UTC")

        # Wait until target time
        while datetime.now() < target_time:
            remaining = (target_time - datetime.now()).total_seconds()
            self.logger.debug(f"Waiting {remaining:.1f}s until {target_time}")
            time.sleep(min(remaining, 1))

        self.logger.info("Time reached — starting buy attempts")

        # Attempt up to 20 buys at 20Hz
        order = None
        status = None
        for attempt in range(1, 21):
            try:
                order = self.http.new_order(
                    symbol=symbol,
                    side="BUY",
                    order_type="MARKET",
                    quote_order_qty=buy_amount
                )
                status = order.get("status")
                self.logger.info(f"[{attempt}/20] BUY order status: {status}")
                if status == "FILLED":
                    break
                elif status == "CANCELED":
                    time.sleep(0.05)
                    continue
                else:
                    self.logger.warning(f"Unexpected status {status}, retrying")
                    time.sleep(0.05)
            except Exception as e:
                self.logger.error(f"[{attempt}/20] BUY exception: {e}")
                time.sleep(0.05)

        if not order or status != "FILLED":
            self.logger.error("All 20 BUY attempts canceled or unfilled — giving up")
            return

        # Log buy info
        executed_qty = float(order["executedQty"])
        cost = float(order["cummulativeQuoteQty"])
        buy_price = cost / executed_qty
        self.logger.info(f"Snipe BUY filled: qty={executed_qty:.6f}, price={buy_price:.6f}")

        # Reuse TP logic for selling
        self.take_profit_trade(symbol, buy_amount, take_profit_pct)
