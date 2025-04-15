import time
import logging
import asyncio
import threading
import hmac
import hashlib
import aiohttp
from datetime import datetime, timezone, timedelta
import pytz


class AsyncMexcClient:
    def __init__(self, api_key, api_secret):
        self.base_url = "https://api.mexc.com"
        self.api_key = api_key
        self.api_secret = api_secret

    def _sign(self, params):
        query_string = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    async def new_order(self, session, symbol, side, order_type, quote_order_qty):
        endpoint = "/api/v3/order"
        url = self.base_url + endpoint

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quoteOrderQty": quote_order_qty,
            "timestamp": timestamp,
            "recvWindow": 5000
        }
        params["signature"] = self._sign(params)

        headers = {
            "X-MEXC-APIKEY": self.api_key
        }

        async with session.post(url, params=params, headers=headers) as resp:
            return await resp.json()

    async def query_order(self, session, symbol, order_id):
        endpoint = "/api/v3/order"
        url = self.base_url + endpoint

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "orderId": order_id,
            "timestamp": timestamp,
            "recvWindow": 5000
        }
        params["signature"] = self._sign(params)

        headers = {
            "X-MEXC-APIKEY": self.api_key
        }

        async with session.get(url, params=params, headers=headers) as resp:
            return await resp.json()


async def burst_sniper_optimized(client, symbol, buy_amount, bursts=3, orders_per_burst=20, delay_between_bursts=0.1):
    async with aiohttp.ClientSession() as session:
        for burst_num in range(1, bursts + 1):
            print(f"🔥 Burst {burst_num}/{bursts} — launching {orders_per_burst} async orders")

            async def try_order():
                try:
                    order = await client.new_order(session, symbol, "BUY", "MARKET", buy_amount)
                    if "orderId" in order:
                        order_id = order["orderId"]
                        status = await client.query_order(session, symbol, order_id)
                        if status.get("status") == "FILLED":
                            print(f"✅ Order filled: qty={status['executedQty']}, price={float(status['cummulativeQuoteQty']) / float(status['executedQty']):.6f}")
                            return status
                except Exception as e:
                    print(f"❌ Order error: {e}")
                return None

            tasks = [asyncio.create_task(try_order()) for _ in range(orders_per_burst)]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in pending:
                task.cancel()

            for task in done:
                result = task.result()
                if result:
                    print("🛑 Stopping — a successful fill was detected.")
                    return result

            print(f"⏳ No fill in burst {burst_num}, waiting {int(delay_between_bursts * 1000)}ms...")
            await asyncio.sleep(delay_between_bursts)

    print("❌ All bursts complete — no successful fills.")
    return None


class MexcSnipeBot:
    def __init__(self, api_key, api_secret):
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        self.api_key = api_key
        self.api_secret = api_secret

    def get_balances(self):
        from pymexc import spot
        self.http = spot.HTTP(api_key=self.api_key, api_secret=self.api_secret)
        try:
            data = self.http.account_information().get("balances", [])
            bal = {b['asset']: float(b['free']) for b in data if float(b['free']) > 0}
            self.logger.info(f"Fetched balances: {bal}")
            return bal
        except Exception as e:
            self.logger.error(f"Error fetching balances: {e}")
            return {}

    def buy_token(self, symbol, quote_amount):
        from pymexc import spot
        self.http = spot.HTTP(api_key=self.api_key, api_secret=self.api_secret)
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
        from pymexc import spot
        self.http = spot.HTTP(api_key=self.api_key, api_secret=self.api_secret)
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
        from pymexc import spot
        self.http = spot.HTTP(api_key=self.api_key, api_secret=self.api_secret)
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

    def snipe_listing(self, symbol, buy_amount, take_profit_pct, target_time_str):
        symbol = symbol.upper()

        # Convert from Dhaka local time (UTC+6) to UTC
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

        # Check USDT balance
        balances = self.get_balances()
        usdt_balance = balances.get("USDT", 0)
        while buy_amount > usdt_balance:
            print(f"\n⚠️ You only have {usdt_balance:.2f} USDT available.")
            try:
                buy_amount = float(input("Enter a new buy amount (≤ your USDT balance): "))
            except ValueError:
                print("❌ Invalid input. Please enter a number.")
                continue
            if buy_amount <= 0:
                print("❌ Buy amount must be positive.")
                continue

        # Wait until 0.5s before listing time
        while datetime.now(timezone.utc) < target_time - timedelta(milliseconds=300):
            time.sleep(0.05)

        self.logger.info("T - 0.7s reached — launching async burst sniper")

        # Create the async client and run optimized burst sniper
        async_client = AsyncMexcClient(self.api_key, self.api_secret)
        filled_order = asyncio.run(burst_sniper_optimized(
            client=async_client,
            symbol=symbol,
            buy_amount=buy_amount,
            bursts=3,
            orders_per_burst=20,
            delay_between_bursts=0.1
        ))

        if not filled_order:
            self.logger.error("All bursts complete — no BUY order filled.")
            return

        executed_qty = float(filled_order["executedQty"])
        cost = float(filled_order["cummulativeQuoteQty"])
        buy_price = cost / executed_qty
        self.logger.info(f"Snipe BUY filled: qty={executed_qty:.6f}, price={buy_price:.6f} USDT")

        # ✅ Start take-profit monitor in background thread immediately
        threading.Thread(
            target=self.monitor_take_profit,
            args=(symbol, executed_qty, buy_price, take_profit_pct),
            daemon=True
        ).start()
        self.logger.info("✅ TP monitoring started in background.")
