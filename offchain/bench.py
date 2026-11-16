import os
import time

import chain
import merkle

challenges = 4
price = 10**16           # 0.01 ETH per period
collateral = 5 * 10**16  # 0.05 ETH


# Alice opens a deal for these parts. Returns the deal number and the gas used.
def open_deal(w3, market, parts, num_periods):
    alice, bob = chain.accounts(w3)
    root = merkle.root(merkle.build_tree(parts))
    tx = market.functions.createDeal(root, len(parts), num_periods, 10, challenges, price, bob, collateral)
    receipt = chain.send(w3, tx, alice, price * num_periods)
    deal_id = market.functions.dealCount().call()
    return deal_id, receipt.gasUsed


# Bob answers the check for one period. Returns the transaction receipt.
def prove_period(w3, market, deal_id, period, parts, levels):
    _, bob = chain.accounts(w3)
    seed_block, _ = market.functions.periodWindow(deal_id, period).call()
    chain.mine_until(w3, seed_block + 1)
    asked = market.functions.getChallenges(deal_id, period).call()

    answer_parts = []
    answer_proofs = []
    for i in asked:
        answer_parts.append(parts[i])
        answer_proofs.append(merkle.prove(levels, i))
    tx = market.functions.submitProof(deal_id, answer_parts, answer_proofs)
    return chain.send(w3, tx, bob)


# Runs a few small deals and records the gas of each contract function.
def gas_per_function(w3, market, blob):
    alice, bob = chain.accounts(w3)
    parts = merkle.chunk(blob)
    levels = merkle.build_tree(parts)
    gas = {}

    # deal 1: 2 periods, Bob proves period 0 and then disappears
    deal_id, gas["createDeal"] = open_deal(w3, market, parts, 2)
    gas["acceptDeal"] = chain.send(w3, market.functions.acceptDeal(deal_id), bob, collateral).gasUsed
    gas["submitProof"] = prove_period(w3, market, deal_id, 0, parts, levels).gasUsed
    _, deadline = market.functions.periodWindow(deal_id, 1).call()
    chain.mine_until(w3, deadline + 1)
    gas["claimFailure"] = chain.send(w3, market.functions.claimFailure(deal_id), alice).gasUsed

    # deal 2: 1 period, proven (the last proof also gives Bob his deposit back)
    deal_id, _ = open_deal(w3, market, parts, 1)
    chain.send(w3, market.functions.acceptDeal(deal_id), bob, collateral)
    gas["submitProof (last period)"] = prove_period(w3, market, deal_id, 0, parts, levels).gasUsed

    # deal 3: Alice cancels before Bob accepts
    deal_id, _ = open_deal(w3, market, parts, 1)
    gas["cancelDeal"] = chain.send(w3, market.functions.cancelDeal(deal_id), alice).gasUsed
    return gas


# One row of the chunk-size table.
def chunk_size_row(w3, market, blob, size):
    _, bob = chain.accounts(w3)

    # time to cut the file and build the tree
    start = time.perf_counter()
    parts = merkle.chunk(blob, size)
    levels = merkle.build_tree(parts)
    tree_ms = (time.perf_counter() - start) * 1000

    deal_id, _ = open_deal(w3, market, parts, 1)
    chain.send(w3, market.functions.acceptDeal(deal_id), bob, collateral)

    # time to build 4 proofs (the work Bob does each period)
    start = time.perf_counter()
    for i in range(challenges):
        merkle.prove(levels, i)
    prove_ms = (time.perf_counter() - start) * 1000

    receipt = prove_period(w3, market, deal_id, 0, parts, levels)
    calldata = len(w3.eth.get_transaction(receipt.transactionHash)["input"])
    depth = len(levels) - 1
    return [size, len(parts), depth, receipt.gasUsed, calldata, round(tree_ms), round(prove_ms, 3)]


def main():
    blob = os.urandom(1024 * 1024)  # 1 MB of random bytes, like an encrypted file
    anvil, w3 = chain.start_anvil(8548)
    try:
        market = chain.deploy(w3)
        gas = gas_per_function(w3, market, blob)
        rows = []
        for size in [256, 1024, 4096]:
            rows.append(chunk_size_row(w3, market, blob, size))
    finally:
        anvil.terminate()
        anvil.wait()

    print("Gas per function (1 MB file, 1024-byte chunks, 4 challenges):")
    print("| function | gas |")
    print("|---|---|")
    for name in gas:
        print(f"| {name} | {gas[name]} |")

    print("\nChunk size (1 MB file, 4 challenges):")
    print("| chunk size | chunks | proof depth | submitProof gas | calldata bytes | tree build (ms) | 4 proofs (ms) |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")


if __name__ == "__main__":
    main()
