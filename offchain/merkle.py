import json
import os

from web3 import Web3

CHUNK_SIZE = 1024


# Divides the file into equal-sized pieces and adds zero padding to the last piece.
def chunk(data, size=CHUNK_SIZE):
    if not data:
        print("file is empty")
        exit()
    parts = []
    for i in range(0, len(data), size):
        part = data[i:i + size]
        if len(part) < size:
            part += b"\0" * (size - len(part))
        parts.append(part)
    return parts


# Creates a hash for one file chunk (0 = leaf, i = its position).
def leaf(i, part):
    return bytes(Web3.solidity_keccak(["uint8", "uint256", "bytes"], [0, i, part]))


# Combines two hashes into one parent hash (1 = inner node).
def node(left, right):
    return bytes(Web3.solidity_keccak(["uint8", "bytes32", "bytes32"], [1, left, right]))


# Builds the full tree. levels[0] = the leaves, levels[-1] = [root].
def build_tree(parts):
    # Add empty chunks until the count is a power of 2, so every level has pairs.
    count = 1
    while count < len(parts):
        count *= 2
    parts = parts + [b"\0" * len(parts[0])] * (count - len(parts))

    # First level: hash every chunk.
    level = []
    for i in range(len(parts)):
        level.append(leaf(i, parts[i]))
    levels = [level]

    # Combine two hashes at a time until only the root remains.
    while len(level) > 1:
        new_level = []
        for i in range(0, len(level), 2):
            new_level.append(node(level[i], level[i + 1]))
        level = new_level
        levels.append(level)
    return levels


# Returns the final hash representing the whole file.
def root(levels):
    return levels[-1][0]


# Returns the sibling hashes needed to prove one chunk belongs to the file.
def prove(levels, i):
    proof = []
    for level in levels[:-1]:
        proof.append(level[i ^ 1])  # i ^ 1 = the sibling's position
        i //= 2
    return proof


# Checks whether a chunk and its proof lead to the root (same check as the contract).
def verify(root_hash, i, part, proof):
    h = leaf(i, part)
    for sibling in proof:
        if i % 2 == 0:
            h = node(h, sibling)
        else:
            h = node(sibling, h)
        i //= 2
    return h == root_hash


# Running this file writes test/vectors.json: a small tree (5 chunks) for the tests.
if __name__ == "__main__":
    data = bytes((i * 7 + 3) % 256 for i in range(4 * CHUNK_SIZE + 100))
    parts = chunk(data)
    levels = build_tree(parts)
    vectors = {
        "root": "0x" + root(levels).hex(),
        "numChunks": len(parts),
        "chunks": ["0x" + p.hex() for p in parts],
        "proofs": [["0x" + h.hex() for h in prove(levels, i)] for i in range(len(parts))],
    }
    with open(os.path.join(os.path.dirname(__file__), "..", "test", "vectors.json"), "w") as f:
        json.dump(vectors, f, indent=1)
    print("wrote test/vectors.json")
