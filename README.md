# Decentralized Storage Marketplace (Task 3, Solidity)

**Alice** has a file and wants **Bob** to store it for her. She does not trust Bob, so:
- she **locks** (encrypts) the file before giving it to him, so he cannot read it,
- she keeps only a short **fingerprint** of the file (the Merkle root),
- she pays into a **smart contract**, not to Bob directly,
- every period the contract asks Bob to show a few **random pieces** of the file,
- if Bob shows them, he gets paid for that period; if he cannot, Alice gets his deposit and her money back.

---

## 1. Files

```
src/StorageMarket.sol     the smart contract: holds the money, picks random pieces, checks proofs, pays
offchain/user.py          Alice: lock the file, make the root, open the deal, get the file back, claim
offchain/provider.py      Bob: check the file, accept the deal, prove each period (can also cheat, for testing)
offchain/merkle.py        the Merkle tree: cut file into pieces, hash them, build proofs, check proofs
offchain/chain.py         helpers: start the local blockchain, deploy the contract, send transactions
offchain/demo.py          the demo: one honest deal and one cheating deal
offchain/bench.py         measurements: gas of each function, comparison of chunk sizes
test/StorageMarket.t.sol  20 tests for the contract
test/test_offchain.py     6 tests for the Python code (Merkle tree + full deals)
test/vectors.json         a small test tree made by merkle.py, used by both test files
lib/forge-std/            Foundry's test library
foundry.toml              Foundry settings
requirements.txt          Python packages
DESIGN_NOTES.md           more detail on every design decision
```

---

## 2. How it works, step by step (and where in the code)

### Step 0: Start the blockchain
`demo.py` starts a local test blockchain (anvil), where Alice and Bob each get 10,000 test ETH,
and puts the contract on it.
- `chain.start_anvil()` starts the chain
- `chain.deploy()` deploys `StorageMarket.sol` (compiled by `forge build`)

### Step 1: Alice prepares her file
Alice locks the file, cuts it into pieces and makes its fingerprint.
- `user.create_deal()`
  - `encrypt()`: locks the file with AES-256-GCM and a random key
  - `merkle.chunk()`: cuts it into 1024-byte pieces (the last one is padded with zeros)
  - `merkle.build_tree()`: hashes each piece (`leaf`) and pairs of hashes (`node`) up to one hash
  - `merkle.root()`: that last hash is the **root**, the fingerprint of the whole file

### Step 2: Alice opens the deal and hands over the file
Alice pays for 3 periods (3 x 0.01 ETH) into the contract and gives Bob the locked file.
She keeps only the root and the key.
- `user.create_deal()` calls the contract's **`createDeal`**, which checks the settings and the payment
  and saves the deal
- the locked file goes to `demo_data/provider_inbox/` (Bob's storage)
- the root and the key go to `demo_data/user/` (all Alice keeps)

### Step 3: Bob checks the file and accepts
Bob rebuilds the root from the file he got. Only if it matches the root on the chain does he
accept and pay his 0.05 ETH deposit. (Otherwise Alice could set him up to fail and take his deposit.)
- `provider.run()`: `merkle.chunk()`, `merkle.build_tree()`, `merkle.root()`, compare with the deal
- contract **`acceptDeal`**: takes the deposit; period 0 starts at this block

### Step 4: Every period, a random check
1. Bob waits until the period's **seed block** exists (`periodWindow`, `chain.mine_until`).
2. The contract uses that block's hash to pick **4 random piece numbers** (**`getChallenges`**).
   Nobody knows them in advance, so Bob has to keep the whole file.
3. Bob builds a proof for each piece (`merkle.prove()`) and sends the pieces and proofs
   (**`submitProof`**).
4. The contract checks each piece with **`verifyChunk`**: hash the piece, go up the tree with the
   proof, and see if it ends at Alice's root.
5. All correct: Bob gets **0.01 ETH** for this period.

### Step 5: The happy ending
After the last period, the contract also gives Bob his deposit back (deal state: **Completed**).
Alice gets her file back and checks it:
- `user.retrieve()`: rebuild the root and compare with the saved root, then `decrypt()` with her key

### Step 6: The cheating ending
Bob deletes the file (or part of it). In the next check he is asked for pieces he does not have,
and he cannot fake them (a fake piece gives a different root). The deadline passes.
- `provider.run(drop_after=1)`: Bob deletes the whole file before period 1
- `provider.run(cheat_fraction=0.5)`: Bob deletes half the pieces at the start
- `user.claim()` calls the contract's **`claimFailure`**, which pays Alice:

  **Bob's deposit (0.05) + 0.01 for every period Bob did not prove**

  (failed in period 0: 0.08, in period 1: 0.07, in period 2: 0.06). Deal state: **Failed**.

Because the deposit (0.05) is more than the whole payment (0.03), cheating always loses Bob money.

---

## 3. How to run

You need **Foundry** and **Python 3.10 to 3.13** (web3 does not install on 3.14 and newer).

```bash
# 1. Install Foundry (once), then open a new terminal
curl -L https://foundry.paradigm.xyz | bash
foundryup

# 2. Python setup (once, inside the project folder)
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 3. Compile and test
forge build
forge test                      # 20 passed
python -m pytest test           # 6 passed

# 4. Demo
python offchain/demo.py                            # 1 MB random file
python offchain/demo.py --file sample_photo.png    # your own file
python offchain/demo.py --cheat-fraction 0.5       # Bob deletes half the file

# 5. Measurements
python offchain/bench.py
```

In every new terminal, run `source .venv/bin/activate` again before the `python` commands.
If you see "something is already running on port 8545", run `pkill anvil`.

---

## 4. Design decisions

| Decision | My choice | Why |
|---|---|---|
| Keeping the file secret | AES-256-GCM, random key, new random nonce | Bob and the chain only ever see locked data; GCM also detects any change |
| Chunk size | 1024 bytes | middle ground: smaller chunks are cheaper on-chain, bigger chunks make the tree faster to build (see measurements) |
| Merkle tree | leaf = hash(0, index, chunk), node = hash(1, left, right) | the 0/1 stops an inner node from being used as a leaf; the index ties each piece to its position |
| Random checks | hash(blockhash(seed block), deal, period, j) % number of chunks | the seed block does not exist when the deal starts, so Bob cannot know the checks in advance |
| Number of checks | 4 per period | if Bob deleted half the file, he is caught with 94% chance per period, 99.98% over 3 periods |
| Deadlines | 10 blocks per period (max 255) | `blockhash` only works for the last 256 blocks |
| Deposit | 0.05 ETH, more than the payment (0.03 ETH) | getting caught always costs Bob more than he could earn |
| Safety | update the deal before sending money, plus a reentrancy lock | stops a contract from calling back in and getting paid twice |

---

## 5. Measurements

From `python offchain/bench.py` (1 MB file, 4 checks per period).
*Machine: (write your machine here, e.g. MacBook, macOS, Python 3.12, Foundry 1.9.0)*

**Gas per function** (1024-byte chunks):

| function | gas |
|---|---|
| createDeal | 184316 |
| acceptDeal | 31726 |
| submitProof | 243130 |
| claimFailure | 52688 |
| submitProof (last period) | 243040 |
| cancelDeal | 49355 |

**Chunk size comparison:**

| chunk size | chunks | proof depth | submitProof gas | calldata bytes | tree build (ms) | 4 proofs (ms) |
|---|---|---|---|---|---|---|
| 256 | 4096 | 12 | 150316 | 3236 | 831 | 0.01 |
| 1024 | 1024 | 10 | 243160 | 6052 | 218 | 0.009 |
| 4096 | 256 | 8 | 722490 | 18084 | 57 | 0.006 |

Smaller chunks mean Bob sends less data, so proofs are cheaper. But there are more pieces, so the
tree takes longer to build. 1024 bytes is in the middle.

---

## 6. Implementation summary

**New vs borrowed**
- **Written by me:** `offchain/merkle.py` (I first wrote it as a class, then simplified it to plain
  functions), and the changes to comments, names and print messages in the other Python files.
- **Written with Claude Code (Anthropic's AI coding assistant) from my design:** the contract,
  `user.py`, `provider.py`, `chain.py`, `demo.py`, `bench.py` and the tests. I then had them
  simplified, and I reviewed, tested and ran everything.
- **Libraries used (no code copied):** forge-std (tests), web3.py (talking to the chain),
  pycryptodome (AES-GCM).
- **Ideas from known sources:** the 0/1 hash prefixes (Certificate Transparency, RFC 6962),
  random spot checks (proofs of retrievability).

**Challenges**
- web3.py would not install on my default Python 3.15, so I used Python 3.12 with `uv`.
- After rewriting `merkle.py` as a class, the other files broke (`module 'merkle' has no
  attribute 'chunk'`), because they call plain functions. I went back to functions with the
  same names.
- Making the Python Merkle tree match the Solidity one byte for byte; `test/vectors.json` checks it.
- `blockhash` only works for the last 256 blocks, which limits the length of a period.

**Missing pieces**
- The optional extension (editing the file during the deal) is not built.

**Known limitations**
- Whoever creates a block has a little influence over its hash, so the randomness is not perfect.
- Random checks catch big losses quickly, but can miss very small ones (try
  `--cheat-fraction 0.001`).
- `claimFailure` has to be called by someone (a contract cannot start itself), so anyone may call it,
  but the money always goes to Alice.
