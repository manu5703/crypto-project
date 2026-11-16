# Storage overhead: how much extra data the system adds on top of the file.
# Run from the project folder:
#     python offchain/overhead.py                        # 1 MB of random bytes
#     python offchain/overhead.py --file sample_photo.png
import argparse
import os

import merkle
import user


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file")
    args = parser.parse_args()
    if args.file:
        with open(args.file, "rb") as f:
            data = f.read()
    else:
        data = os.urandom(1024 * 1024)

    original = len(data)

    # 1. encryption adds a 12-byte nonce and a 16-byte tag
    blob = user.encrypt(data, os.urandom(32))
    encrypted = len(blob)

    # 2. the last chunk is padded with zeros
    parts = merkle.chunk(blob)
    chunked = len(parts) * merkle.CHUNK_SIZE

    # 3. the tree: leaves padded to a power of 2, every hash is 32 bytes
    levels = merkle.build_tree(parts)
    leaves = len(levels[0])
    hashes = 0
    for level in levels:
        hashes += len(level)
    tree_bytes = hashes * 32
    depth = len(levels) - 1

    # 4. what Bob sends each period: 4 chunks, each with a path of `depth` hashes
    proof_bytes = user.challenges * (merkle.CHUNK_SIZE + depth * 32)

    def pct(x):
        return f"{100 * x / original:.3f}%"

    print(f"file size: {original} bytes, chunk size: {merkle.CHUNK_SIZE} bytes\n")
    print("| what | bytes | overhead vs the file |")
    print("|---|---|---|")
    print(f"| original file | {original} | - |")
    print(f"| encrypted file (+ nonce + tag) | {encrypted} | +{encrypted - original} ({pct(encrypted - original)}) |")
    print(f"| padding of the last chunk | {chunked - encrypted} | {pct(chunked - encrypted)} |")
    print(f"| Merkle tree ({len(parts)} chunks, {leaves} leaves, {hashes} hashes) | {tree_bytes} | {pct(tree_bytes)} |")
    print(f"| Bob stores: encrypted file + tree | {encrypted + tree_bytes} | {pct(encrypted + tree_bytes - original)} |")
    print(f"| Alice keeps: root + key | 64 | {pct(64)} |")
    print(f"| sent each period: {user.challenges} chunks + paths (depth {depth}) | {proof_bytes} | {pct(proof_bytes)} |")
    print(f"| sent over the whole deal ({user.num_periods} periods) | {proof_bytes * user.num_periods} | {pct(proof_bytes * user.num_periods)} |")


if __name__ == "__main__":
    main()
