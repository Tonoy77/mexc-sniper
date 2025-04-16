import time
import logging
import asyncio
import threading
import hmac
import hashlib
import aiohttp
from datetime import datetime, timezone, timedelta
import pytz
from pymexc import spot


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
        headers = {"X-MEXC-APIKEY": self.api_key}

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
        headers = {"X-MEXC-APIKEY": self.api_key}

        async with session.get(url, params=params, headers=headers) as resp:
            return await resp.json()

    async def cancel_order(self, session, symbol, order_id):
        url = self.base_url + "/api/v3/order"
        timestamp = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "orderId": order_id,
            "timestamp": timestamp,
            "recvWindow": 5000
        }
        params["signature"] = self._sign(params)
        headers = {"X-MEXC-APIKEY": self.api_key}
        async with session.delete(url, params=params, headers=headers) as resp:
            return await resp.json()


class MexcSnipeBot:
    def __init__(self, api_key, api_secret):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        self.api_key = api_key
        self.api_secret = api_secret
        self.http = spot.HTTP(api_key=self.api_key, api_secret=self.api_secret)

    def get_balances(self):
        try:
            data = self.http.account_information().get("balances", [])
            return {b['asset']: float(b['free']) for b in data if float(b['free']) > 0}
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

    def monitor_take_profit(self, symbol, qty, buy_price, tp_pct):
        target = buy_price * (1 + tp_pct / 100)
        self.logger.info(f"TP set at {tp_pct}% → {target:.6f} USDT")

        while True:
            try:
                price = float(self.http.ticker_price(symbol=symbol)['price'])
                if price >= target:
                    self.logger.info(f"TP hit ({price:.6f} ≥ {target:.6f}), selling {qty}")
                    sell = self.sell_token(symbol, qty)
                    if sell:
                        time.sleep(0.5)
                        status = self.http.query_order(symbol=symbol, order_id=sell['orderId'])
                        sell_price = float(status['cummulativeQuoteQty']) / float(status['executedQty'])
                        profit = (sell_price - buy_price) * qty
                        self.logger.info(f"Sold at {sell_price:.6f}, profit {profit:.6f} USDT ({(sell_price / buy_price - 1) * 100:.2f}%)")
                    break
                time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"TP error: {e}")
                break

    def snipe_listing(self, symbol, buy_amount, tp_pct, target_time_str):
        import aiohttp

        symbol = symbol.upper()
        try:
            target_time = pytz.timezone("Asia/Dhaka").localize(
                datetime.strptime(target_time_str, "%Y-%m-%d %H:%M:%S")
            ).astimezone(timezone.utc)
        except Exception as e:
            self.logger.error(f"Invalid time: {e}")
            return

        self.logger.info(f"Sniping {symbol} at {target_time.strftime('%H:%M:%S')} UTC")

        if buy_amount > self.get_balances().get("USDT", 0):
            self.logger.error("Not enough USDT")
            return

        async def sniper_with_cancel():
            client = AsyncMexcClient(self.api_key, self.api_secret)
            async with aiohttp.ClientSession() as session:
                start = target_time - timedelta(seconds=0.5)
                end = target_time + timedelta(seconds=0.7)
                while datetime.now(timezone.utc) < start:
                    await asyncio.sleep(0.005)

                order_ids = []
                filled = None

                while datetime.now(timezone.utc) < end:
                    if filled:
                        break
                    try:
                        order = await client.new_order(session, symbol, "BUY", "MARKET", buy_amount)
                        if "orderId" in order:
                            oid = order["orderId"]
                            order_ids.append(oid)
                            status = await client.query_order(session, symbol, oid)
                            if status.get("status") == "FILLED":
                                filled = status
                                break
                    except:
                        continue

                if filled:
                    self.logger.info(f"Filled order: {filled['orderId']} — cancelling others...")
                    for oid in order_ids:
                        if oid != filled["orderId"]:
                            try:
                                await client.cancel_order(session, symbol, oid)
                            except:
                                continue
                return filled

        filled = asyncio.run(sniper_with_cancel())

        if not filled:
            self.logger.error("❌ No orders were filled")
            return

        qty = float(filled["executedQty"])
        cost = float(filled["cummulativeQuoteQty"])
        price = cost / qty
        self.logger.info(f"✅ Sniped {qty:.6f} @ {price:.6f} USDT")

        threading.Thread(
            target=self.monitor_take_profit,
            args=(symbol, qty, price, tp_pct),
            daemon=True
        ).start()
        self.logger.info("✅ TP monitor started")
