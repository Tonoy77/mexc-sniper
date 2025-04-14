import json
import sys
from bot import MexcSnipeBot

def load_config():
    try:
        with open("config.json") as f:
            return json.load(f)
    except Exception as e:
        print(f"Config load error: {e}")
        sys.exit(1)

def main():
    cfg = load_config()
    bot = MexcSnipeBot(cfg["api_key"], cfg["api_secret"])

    while True:
        print("\n1) Snipe listing")
        print("2) TP trade (existing pair)")
        print("3) Manual BUY")
        print("4) Manual SELL")
        print("5) Check Balances")
        print("q) Quit")
        choice = input("Select: ").strip().lower()

        if choice == "1":
            symbol = input("Token (e.g. ABCUSDT): ")
            amount = float(input("Buy amount (USDT): "))
            tp = float(input("Take-profit %: "))
            tstr = input("Buy time (YYYY-MM-DD HH:MM:SS UTC+6): ")
            bot.snipe_listing(symbol, amount, tp, tstr)

        elif choice == "2":
            symbol = input("Token (e.g. ABCUSDT): ")
            amount = float(input("Buy amount (USDT): "))
            tp = float(input("Take-profit %: "))
            bot.take_profit_trade(symbol, amount, tp)

        elif choice == "3":
            symbol = input("Token (e.g. ABCUSDT): ")
            amount = float(input("Buy amount (USDT): "))
            bot.buy_token(symbol, amount)

        elif choice == "4":
            bals = bot.get_balances()
            print("Balances:")
            for asset, bal in bals.items():
                print(f"  {asset}: {bal:.6f}")
                
            symbol = input("Token (e.g. ABCUSDT): ")
            qty = float(input("Quantity to sell: "))
            bot.sell_token(symbol, qty)

        elif choice == "5":
            bals = bot.get_balances()
            print("Balances:")
            for asset, bal in bals.items():
                print(f"  {asset}: {bal:.6f}")

        elif choice == "q":
            print("Goodbye!")
            break

        else:
            print("Invalid option.")

if __name__ == "__main__":
    main()
