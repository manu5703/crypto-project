"""Small web3 helpers used by user.py, provider.py, demo.py, bench.py and the tests."""
import json
import os
import shutil
import subprocess
import time

from web3 import Web3

PROJECT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
ARTIFACT = os.path.join(PROJECT_DIR, "out", "StorageMarket.sol", "StorageMarket.json")

# getDeal() returns the Deal struct as a plain tuple, in this order.
DEAL_FIELDS = ["user", "provider", "root", "numChunks", "numPeriods", "periodBlocks",
               "challengesPerPeriod", "depth", "state", "nextPeriod", "startBlock",
               "pricePerPeriod", "collateral"]
STATES = ["None", "Proposed", "Active", "Completed", "Failed", "Cancelled"]


def start_anvil(port=8545):
    """Start a fresh local chain. Returns (process, w3). The caller must stop the process."""
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    if w3.is_connected():
        raise RuntimeError(f"something is already running on port {port}, stop it first")
    anvil = shutil.which("anvil") or os.path.expanduser("~/.foundry/bin/anvil")
    proc = subprocess.Popen([anvil, "--port", str(port), "--silent"])
    for _ in range(100):
        if w3.is_connected():
            return proc, w3
        time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("anvil did not start")


def accounts(w3):
    """anvil's default accounts: 0 is the user, 1 is the provider."""
    return w3.eth.accounts[0], w3.eth.accounts[1]


def deploy(w3):
    if not os.path.exists(ARTIFACT):
        raise SystemExit("contract not compiled: run `forge build` first")
    with open(ARTIFACT) as f:
        artifact = json.load(f)
    abi, bytecode = artifact["abi"], artifact["bytecode"]["object"]
    user, _ = accounts(w3)
    tx = w3.eth.contract(abi=abi, bytecode=bytecode).constructor().transact({"from": user})
    address = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    return w3.eth.contract(address=address, abi=abi)


def send(w3, fn, sender, value=0):
    """Send a contract call as a transaction. Raises if it reverts."""
    tx = fn.transact({"from": sender, "value": value})
    receipt = w3.eth.wait_for_transaction_receipt(tx)
    if receipt.status != 1:
        raise RuntimeError("transaction reverted")
    return receipt


def get_deal(market, deal_id):
    return dict(zip(DEAL_FIELDS, market.functions.getDeal(deal_id).call()))


def mine_until(w3, block):
    """Mine empty blocks until the chain reaches `block` (this simulates waiting)."""
    n = block - w3.eth.block_number
    if n > 0:
        w3.provider.make_request("anvil_mine", [hex(n)])


def eth(wei):
    return f"{Web3.from_wei(wei, 'ether'):.4f} ETH"
