"""Measurements for the README. Run from the project folder after `forge build`:
    python offchain/bench.py

1. Gas of every contract function, from real transactions on a local chain
   (1 MB file, 1024-byte chunks, 4 challenges).
2. submitProof gas, calldata size and off-chain time for different chunk sizes.
"""
import os
import time

import chain
import merkle

K = 4
PRICE = 10**16
COLLATERAL = 5 * 10**16


def open_deal(w3, market, chunks, num_periods):
    user, prov = chain.accounts(w3)
    root = merkle.root(merkle.build_tree(chunks))
    fn = market.functions.createDeal(root, len(chunks), num_periods, 10, K, PRICE, prov, COLLATERAL)
    receipt = chain.send(w3, fn, user, PRICE * num_periods)
    return market.functions.dealCount().call(), receipt.gasUsed


def prove_period(w3, market, deal, p, chunks, levels):
    _, prov = chain.accounts(w3)
    seed_block, _ = market.functions.periodWindow(deal, p).call()
    chain.mine_until(w3, seed_block + 1)
    asked = market.functions.getChallenges(deal, p).call()
    proofs = [merkle.prove(levels, i) for i in asked]
    return chain.send(w3, market.functions.submitProof(deal, [chunks[i] for i in asked], proofs), prov)


def gas_per_function(w3, market, blob):
    user, prov = chain.accounts(w3)
    chunks = merkle.chunk(blob)
    levels = merkle.build_tree(chunks)
    gas = {}

    # Deal A: 2 periods, the provider proves period 0, then disappears.
    deal, gas["createDeal"] = open_deal(w3, market, chunks, 2)
    gas["acceptDeal"] = chain.send(w3, market.functions.acceptDeal(deal), prov, COLLATERAL).gasUsed
    gas["submitProof (not last period)"] = prove_period(w3, market, deal, 0, chunks, levels).gasUsed
    _, deadline = market.functions.periodWindow(deal, 1).call()
    chain.mine_until(w3, deadline + 1)
    gas["claimFailure"] = chain.send(w3, market.functions.claimFailure(deal), user).gasUsed

    # Deal B: 1 period, proven (last period, so the collateral is returned too).
    deal, _ = open_deal(w3, market, chunks, 1)
    chain.send(w3, market.functions.acceptDeal(deal), prov, COLLATERAL)
    gas["submitProof (last period)"] = prove_period(w3, market, deal, 0, chunks, levels).gasUsed

    # Deal C: cancelled before the provider accepts.
    deal, _ = open_deal(w3, market, chunks, 1)
    gas["cancelDeal"] = chain.send(w3, market.functions.cancelDeal(deal), user).gasUsed
    return gas


def chunk_size_row(w3, market, blob, size):
    start = time.perf_counter()
    chunks = merkle.chunk(blob, size)
    levels = merkle.build_tree(chunks)
    tree_ms = (time.perf_counter() - start) * 1000

    deal, _ = open_deal(w3, market, chunks, 1)
    _, prov = chain.accounts(w3)
    chain.send(w3, market.functions.acceptDeal(deal), prov, COLLATERAL)

    start = time.perf_counter()
    for i in range(K):  # time to build K proofs (same work as answering one period)
        merkle.prove(levels, i)
    prove_ms = (time.perf_counter() - start) * 1000

    receipt = prove_period(w3, market, deal, 0, chunks, levels)
    calldata = len(w3.eth.get_transaction(receipt.transactionHash)["input"])
    return [size, len(chunks), len(levels) - 1, receipt.gasUsed, calldata, f"{tree_ms:.0f}", f"{prove_ms:.3f}"]


def main():
    blob = os.urandom(1024 * 1024)  # treated as the encrypted file
    anvil, w3 = chain.start_anvil(8548)
    try:
        market = chain.deploy(w3)
        gas = gas_per_function(w3, market, blob)
        rows = [chunk_size_row(w3, market, blob, s) for s in [256, 1024, 4096]]
    finally:
        anvil.terminate()
        anvil.wait()

    print(f"Gas per function (real transactions, 1 MB file, {merkle.CHUNK_SIZE}-byte chunks, k = {K}):")
    print("| function | gas |")
    print("|---|---|")
    for name, g in gas.items():
        print(f"| {name} | {g:,} |")

    print(f"\nChunk size (1 MB file, k = {K}):")
    print("| chunk size | chunks | proof depth | submitProof gas | calldata bytes | tree build (ms) | 4 proofs (ms) |")
    print("|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(f"{x:,}" if isinstance(x, int) else x for x in row) + " |")


if __name__ == "__main__":
    main()
