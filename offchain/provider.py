
import random
# import files
import chain
import merkle


# Returns how many periods Bob proved.
def run(w3, market, deal_id, folder, drop_after=None, cheat_fraction=0.0):
    # F
    alice, bob = chain.accounts(w3)
    deal = chain.get_deal(market, deal_id)

    # read the locked file Alice sent and rebuild the tree
    with open(f"{folder}/provider_inbox/deal_{deal_id}.bin", "rb") as f:
        blob = f.read()
    parts = merkle.chunk(blob)
    levels = merkle.build_tree(parts)

    # check the file matches the deal first, so Alice can't set Bob up to fail and take his deposit (0.05ETH)
    if merkle.root(levels) != deal["root"] or len(parts) != deal["numChunks"]:
        print(f"[provider] deal {deal_id}: file does not match the root, not accepting")
        exit()

    tx = market.functions.acceptDeal(deal_id)
    chain.send(w3, tx, bob, deal["collateral"])
    print(f"[provider] deal {deal_id}: root checked, accepted with {chain.eth(deal['collateral'])} collateral")

    # the chunk numbers Bob still has
    kept = set(range(len(parts)))
    
    # cheating by deleting some chunks
    if cheat_fraction > 0:
        how_many = int(cheat_fraction * len(parts))
        deleted = random.sample(range(len(parts)), how_many)
        kept = kept - set(deleted)
        print(f"[provider] CHEATING: deleted {how_many} of {len(parts)} chunks")
        
    # one round per period, (loops on 3 periods)
    for period in range(deal["numPeriods"]):
        if period == drop_after:
            kept = set()
            print(f"[provider] CHEATING: deleted the whole file before period {period}")

        # wait for the seed block, then ask which chunks are checked
        seed_block, _ = market.functions.periodWindow(deal_id, period).call()
        chain.mine_until(w3, seed_block + 1)
        asked = market.functions.getChallenges(deal_id, period).call()

        # if any asked chunk is missing, Bob cannot make a valid proof
        for i in asked:
            if i not in kept:
                print(f"[provider] period {period}: asked for chunks {asked}, some are missing -> cannot prove")
                return period

        # send the asked chunks with their Merkle paths
        answer_parts = []
        answer_proofs = []
        for i in asked:
            answer_parts.append(parts[i])
            answer_proofs.append(merkle.prove(levels, i))
        tx = market.functions.submitProof(deal_id, answer_parts, answer_proofs)
        receipt = chain.send(w3, tx, bob)
        print(f"[provider] period {period}: asked for chunks {asked}, proof accepted (gas {receipt.gasUsed})")

    return deal["numPeriods"]
