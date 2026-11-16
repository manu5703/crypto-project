# User (Alice): locks the file, makes its root, opens the deal, gets the file back,
# and claims Bob's deposit if he fails a check.
import json
import os

from Crypto.Cipher import AES
from web3 import Web3

import chain
import merkle

# deal settings
num_periods = 3
period_blocks = 10                       # length of one period, in blocks
challenges = 4                           # chunks checked per period
price = Web3.to_wei(0.01, "ether")       # paid per period
collateral = Web3.to_wei(0.05, "ether")  # Bob's deposit, more than the whole payment


# Lock the file with AES-GCM. Saved as nonce + ciphertext + tag.
def encrypt(data, key):
    nonce = os.urandom(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return nonce + ciphertext + tag


# Unlock the file. Gives an error if even one byte was changed.
def decrypt(blob, key):
    nonce = blob[:12]
    ciphertext = blob[12:-16]
    tag = blob[-16:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag)


def create_deal(w3, market, data, folder):
    alice, bob = chain.accounts(w3)

    # lock the file and make its fingerprint (the Merkle root)
    key = os.urandom(32)
    blob = encrypt(data, key)
    parts = merkle.chunk(blob)
    root = merkle.root(merkle.build_tree(parts))

    # open the deal and pay for all periods
    tx = market.functions.createDeal(root, len(parts), num_periods, period_blocks,
                                     challenges, price, bob, collateral)
    chain.send(w3, tx, alice, price * num_periods)
    deal_id = market.functions.dealCount().call()

    # give the locked file to Bob
    os.makedirs(f"{folder}/provider_inbox", exist_ok=True)
    with open(f"{folder}/provider_inbox/deal_{deal_id}.bin", "wb") as f:
        f.write(blob)

    # Alice keeps only the root and the key
    os.makedirs(f"{folder}/user", exist_ok=True)
    with open(f"{folder}/user/deal_{deal_id}.json", "w") as f:
        json.dump({"root": root.hex(), "key": key.hex()}, f)

    print(f"[user] deal {deal_id}: {len(data)} bytes -> {len(parts)} encrypted chunks, root 0x{root.hex()}")
    return deal_id


def retrieve(folder, deal_id):
    with open(f"{folder}/user/deal_{deal_id}.json") as f:
        saved = json.load(f)
    with open(f"{folder}/provider_inbox/deal_{deal_id}.bin", "rb") as f:  # download from Bob
        blob = f.read()

    # check the file against the root before unlocking it
    root = merkle.root(merkle.build_tree(merkle.chunk(blob)))
    if root.hex() != saved["root"]:
        print("file does not match the root")
        exit()
    data = decrypt(blob, bytes.fromhex(saved["key"]))

    print(f"[user] deal {deal_id}: root OK, decrypted {len(data)} bytes, file is unchanged")
    return data


def claim(w3, market, deal_id):
    alice, _ = chain.accounts(w3)

    # wait until the missed deadline has passed
    period = chain.get_deal(market, deal_id)["nextPeriod"]
    _, deadline = market.functions.periodWindow(deal_id, period).call()
    chain.mine_until(w3, deadline + 1)

    before = w3.eth.get_balance(alice)
    receipt = chain.send(w3, market.functions.claimFailure(deal_id), alice)
    fee = receipt.gasUsed * receipt.effectiveGasPrice  # what Alice paid to send the claim
    received = w3.eth.get_balance(alice) - before + fee

    print(f"[user] deal {deal_id}: provider missed period {period}, claimFailure paid {chain.eth(received)} to the user")
    return received
