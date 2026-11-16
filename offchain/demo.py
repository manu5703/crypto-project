
import argparse
import os
import shutil

import chain
import provider
import user


def show_balances(w3):
    u, p = chain.accounts(w3)
    print(f"  user:     {chain.eth(w3.eth.get_balance(u))}")
    print(f"  provider: {chain.eth(w3.eth.get_balance(p))}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file")
    parser.add_argument("--cheat-fraction", type=float, default=0.0)
    args = parser.parse_args()

    if args.file:
        with open(args.file, "rb") as f:
            data = f.read()
    else:
        data = os.urandom(1024 * 1024)

    folder = "demo_data"
    shutil.rmtree(folder, ignore_errors=True)  # start with an empty folder

    anvil, w3 = chain.start_anvil()
    try:
        market = chain.deploy(w3)
        print("Contract deployed at", market.address)

        # Deal 1: Bob is honest
        print("\n--- Deal 1: honest provider ---")
        print("Balances before:")
        show_balances(w3)
        deal = user.create_deal(w3, market, data, folder)
        provider.run(w3, market, deal, folder)
        user.retrieve(folder, deal)
        print("Balances after (Bob earned 3 x 0.01 ETH):")
        show_balances(w3)

        # Deal 2: Bob cheats
        print("\n--- Deal 2: cheating provider ---")
        deal = user.create_deal(w3, market, data, folder)

        if args.cheat_fraction > 0:
            # Bob deletes part of the file
            periods_proven = provider.run(w3, market, deal, folder, cheat_fraction=args.cheat_fraction)
        else:
            # Bob deletes the whole file after period 0
            periods_proven = provider.run(w3, market, deal, folder, drop_after=1)

        if periods_proven < user.num_periods:
            print("Bob got caught")
            user.claim(w3, market, deal)
            print("Balances after (Bob lost his 0.05 ETH deposit):")
        else:
            print("Bob got lucky: no missing chunk was checked")
            print("Balances after:")
        show_balances(w3)

        # How each deal ended
        print("\nDeal 1:", chain.STATES[chain.get_deal(market, 1)["state"]])
        print("Deal 2:", chain.STATES[chain.get_deal(market, 2)["state"]])
    finally:
        # always stop the local chain, even if something crashed
        anvil.terminate()
        anvil.wait()

if __name__ == "__main__":
    main()
