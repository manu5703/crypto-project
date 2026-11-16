# Python tests. Run with:  python -m pytest test   (after `forge build`)
import json
import os
import sys

import pytest
from web3 import Web3

# let the tests import the files in offchain/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "offchain"))
import chain     # noqa: E402
import merkle    # noqa: E402
import provider  # noqa: E402
import user      # noqa: E402


# ---------- Merkle tree ----------

# the Python tree gives the same root and proofs as test/vectors.json
# (the contract checks the same file in test_MerkleVectorsFromPython)
def test_root_matches_solidity_vector():
    with open(os.path.join(os.path.dirname(__file__), "vectors.json")) as f:
        vec = json.load(f)
    parts = [bytes.fromhex(c[2:]) for c in vec["chunks"]]
    levels = merkle.build_tree(parts)
    assert "0x" + merkle.root(levels).hex() == vec["root"]
    for i in range(len(parts)):
        proof = merkle.prove(levels, i)
        assert ["0x" + h.hex() for h in proof] == vec["proofs"][i]
        assert merkle.verify(merkle.root(levels), i, parts[i], proof)


# any change makes a proof fail
def test_tampered_proof_fails():
    parts = merkle.chunk(os.urandom(5000))
    levels = merkle.build_tree(parts)
    root = merkle.root(levels)
    proof = merkle.prove(levels, 3)
    assert merkle.verify(root, 3, parts[3], proof)                       # correct
    assert not merkle.verify(root, 3, b"\1" + parts[3][1:], proof)       # changed chunk
    assert not merkle.verify(root, 2, parts[3], proof)                   # wrong position
    assert not merkle.verify(root, 3, parts[3], proof[:-1])              # proof too short
    assert not merkle.verify(root, 3, parts[3], proof + [proof[0]])      # proof too long
    bad = list(proof)
    bad[0] = bytes(32)
    assert not merkle.verify(root, 3, parts[3], bad)                     # changed sibling hash


# the last chunk is padded with zeros up to 1024 bytes
def test_last_chunk_is_padded():
    parts = merkle.chunk(b"a" * 1025)
    assert len(parts) == 2
    assert parts[1] == b"a" + b"\0" * 1023


# ---------- full deals on a local chain ----------

# starts a fresh local chain for each test below, and stops it afterwards
@pytest.fixture
def setup(tmp_path):
    anvil, w3 = chain.start_anvil(8547)
    try:
        yield w3, chain.deploy(w3), str(tmp_path)
    finally:
        anvil.terminate()
        anvil.wait()


# honest Bob with a 1 MB file: he is paid, Alice gets her file back
def test_honest_provider_is_paid(setup):
    w3, market, folder = setup
    _, bob = chain.accounts(w3)
    data = os.urandom(1024 * 1024)
    deal = user.create_deal(w3, market, data, folder)
    before = w3.eth.get_balance(bob)

    assert provider.run(w3, market, deal, folder) == 3
    assert chain.STATES[chain.get_deal(market, deal)["state"]] == "Completed"
    earned = w3.eth.get_balance(bob) - before
    # 3 x 0.01 ETH, minus the gas Bob paid
    assert Web3.to_wei(0.025, "ether") < earned <= Web3.to_wei(0.03, "ether")
    assert w3.eth.get_balance(market.address) == 0
    assert user.retrieve(folder, deal) == data


# Bob deletes the file after period 0: Alice gets 0.05 + 2 x 0.01
def test_cheating_provider_loses_collateral(setup):
    w3, market, folder = setup
    deal = user.create_deal(w3, market, os.urandom(1024 * 1024), folder)
    assert provider.run(w3, market, deal, folder, drop_after=1) == 1
    assert user.claim(w3, market, deal) == Web3.to_wei(0.07, "ether")
    assert chain.STATES[chain.get_deal(market, deal)["state"]] == "Failed"
    assert w3.eth.get_balance(market.address) == 0


# Bob refuses a file that does not match the root (one bit changed)
def test_provider_refuses_wrong_file(setup):
    w3, market, folder = setup
    deal = user.create_deal(w3, market, os.urandom(10_000), folder)
    path = f"{folder}/provider_inbox/deal_{deal}.bin"
    with open(path, "rb") as f:
        blob = bytearray(f.read())
    blob[0] ^= 1
    with open(path, "wb") as f:
        f.write(blob)

    with pytest.raises(SystemExit):
        provider.run(w3, market, deal, folder)
    assert chain.STATES[chain.get_deal(market, deal)["state"]] == "Proposed"  # never accepted
