import time
import logging
from datetime import datetime, timezone
from pymexc import spot
import pytz

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

    def monitor_take_profit(self, symbol, executed_qty, buy_price, take_profit_pct):
        """Monitors price and sells when TP % is hit."""
        target_price = buy_price * (1 + take_profit_pct / 100)
        self.logger.info(f"TP set at {take_profit_pct}% → {target_price:.6f} USDT")
        while True:
            try:
                ticker = self.http.ticker_price(symbol=symbol)
                current = float(ticker['price'])
                self.logger.debug(f"Current price: {current:.6f}")
                if current >= target_price:
                    self.logger.info(f"TP hit ({current:.6f} ≥ {target_price:.6f}), selling {executed_qty}")
                    sell_order = self.sell_token(symbol, executed_qty)
                    if sell_order:
                        time.sleep(0.5)
                        status = self.http.query_order(symbol=symbol, order_id=sell_order['orderId'])
                        sell_price = float(status['cummulativeQuoteQty']) / float(status['executedQty'])
                        profit = (sell_price - buy_price) * executed_qty
                        profit_pct = (sell_price / buy_price - 1) * 100
                        self.logger.info(
                            f"Sold at {sell_price:.6f}, profit {profit:.6f} USDT ({profit_pct:.2f}%)"
                        )
                    break
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Error in TP monitoring: {e}")
                break

    def take_profit_trade(self, symbol, buy_amount, take_profit_pct):
        """Market-buy a token, then monitor until take-profit hit."""
        symbol = symbol.upper()
        self.logger.info(f"TP Trade: buying {buy_amount} USDT of {symbol}")
        buy_order = self.buy_token(symbol, buy_amount)
        if not buy_order:
            self.logger.error("Buy failed, aborting TP trade")
            return

        filled = self.http.query_order(symbol=symbol, order_id=buy_order['orderId'])
        qty = float(filled['executedQty'])
        cost = float(filled['cummulativeQuoteQty'])
        buy_price = cost / qty
        self.logger.info(f"Bought {qty:.6f} {symbol} at {buy_price:.6f} USDT")

        self.monitor_take_profit(symbol, qty, buy_price, take_profit_pct)

    def snipe_listing(self, symbol, buy_amount, take_profit_pct, target_time_str):
        symbol = symbol.upper()

        # Convert from Dhaka local time to UTC
        dhaka = pytz.timezone("Asia/Dhaka")
        try:
            local_dt = dhaka.localize(datetime.strptime(target_time_str, "%Y-%m-%d %H:%M:%S"))
            target_time = local_dt.astimezone(timezone.utc)
        except Exception as e:
            self.logger.error(f"Invalid datetime format: {e}")
            return

        self.logger.info(f"Snipe scheduled for {symbol}")
        self.logger.info(f"Local time input: {target_time_str} (Asia/Dhaka)")
        self.logger.info(f"Converted UTC time: {target_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")

        # Wait until exact UTC time
        while datetime.now(timezone.utc) < target_time:
            remaining = (target_time - datetime.now(timezone.utc)).total_seconds()
            self.logger.debug(f"Waiting {remaining:.1f}s until {target_time}")
            time.sleep(min(remaining, 1))

        self.logger.info("Time reached — starting buy attempts")

        # Attempt up to 20 market buys at 20Hz (realistically slower due to network calls)
        filled_order = None

        for attempt in range(1, 21):
            try:
                order = self.http.new_order(
                    symbol=symbol,
                    side="BUY",
                    order_type="MARKET",
                    quote_order_qty=buy_amount
                )
                order_id = order.get("orderId")
                self.logger.info(f"[{attempt}/20] BUY order submitted: orderId={order_id}")

                if order_id:
                    status_info = self.http.query_order(symbol=symbol, order_id=order_id)
                    status = status_info.get("status")
                    self.logger.info(f"[{attempt}/20] Queried order status: {status}")

                    if status == "FILLED":
                        filled_order = status_info
                        break
                    elif status == "CANCELED":
                        time.sleep(0.05)
                        continue
                    else:
                        self.logger.warning(f"Unexpected status {status}, retrying...")
                else:
                    self.logger.warning(f"[{attempt}/20] No orderId returned, skipping...")
                time.sleep(0.05)
            except Exception as e:
                self.logger.error(f"[{attempt}/20] BUY exception: {e}")
                time.sleep(0.05)

        if not filled_order:
            self.logger.error("All 20 BUY attempts failed or were not filled — giving up")
            return

        executed_qty = float(filled_order["executedQty"])
        cost = float(filled_order["cummulativeQuoteQty"])
        buy_price = cost / executed_qty
        self.logger.info(f"Snipe BUY filled: qty={executed_qty:.6f}, price={buy_price:.6f} USDT")

        self.monitor_take_profit(symbol, executed_qty, buy_price, take_profit_pct)
