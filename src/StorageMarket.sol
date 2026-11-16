// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Storage marketplace.
// Alice (user) pays for all periods at the start. Every period, Bob (provider) must
// prove he still has a few random chunks of the file. Each good proof pays him for
// one period. If he misses a deadline, Alice gets his deposit and her unused money.
contract StorageMarket {
    enum State { None, Proposed, Active, Completed, Failed, Cancelled }

    struct Deal {
        address user;
        address provider;
        bytes32 root;              // Merkle root of the encrypted file
        uint64 numChunks;          // number of real chunks
        uint32 numPeriods;
        uint32 periodBlocks;       // length of one period, in blocks
        uint8 challengesPerPeriod;
        uint8 depth;               // number of hashes in each proof
        State state;
        uint32 nextPeriod;         // the period Bob has to prove next
        uint64 startBlock;         // block where Bob accepted
        uint256 pricePerPeriod;
        uint256 collateral;        // Bob's deposit
    }

    mapping(uint256 => Deal) deals;
    uint256 public dealCount;
    bool private locked;

    // Stops a function that sends ETH from being called again while it is still running.
    modifier nonReentrant() {
        require(!locked, "reentrant call");
        locked = true;
        _;
        locked = false;
    }

    // Alice opens a deal and pays for all periods.
    function createDeal(
        bytes32 root,
        uint64 numChunks,
        uint32 numPeriods,
        uint32 periodBlocks,
        uint8 challengesPerPeriod,
        uint256 pricePerPeriod,
        address provider,
        uint256 collateral
    ) external payable returns (uint256 dealId) {
        require(numChunks > 0 && numPeriods > 0, "bad params");
        // blockhash only works for the last 256 blocks, so a period can be at most 255 blocks
        require(periodBlocks >= 2 && periodBlocks <= 255, "bad period length");
        require(challengesPerPeriod >= 1 && challengesPerPeriod <= 16, "bad challenge count");
        require(provider != address(0), "bad provider");
        require(msg.value == pricePerPeriod * numPeriods, "wrong payment");

        dealCount = dealCount + 1;
        dealId = dealCount;
        Deal storage deal = deals[dealId];
        deal.user = msg.sender;
        deal.provider = provider;
        deal.root = root;
        deal.numChunks = numChunks;
        deal.numPeriods = numPeriods;
        deal.periodBlocks = periodBlocks;
        deal.challengesPerPeriod = challengesPerPeriod;
        deal.depth = treeDepth(numChunks);
        deal.state = State.Proposed;
        deal.pricePerPeriod = pricePerPeriod;
        deal.collateral = collateral;
    }

    // Alice takes her money back if Bob has not accepted yet.
    function cancelDeal(uint256 dealId) external nonReentrant {
        Deal storage deal = deals[dealId];
        require(msg.sender == deal.user, "not user");
        require(deal.state == State.Proposed, "wrong state");
        deal.state = State.Cancelled;
        sendEth(deal.user, deal.pricePerPeriod * deal.numPeriods);
    }

    // Bob accepts and pays his deposit. Period 0 starts now.
    function acceptDeal(uint256 dealId) external payable {
        Deal storage deal = deals[dealId];
        require(msg.sender == deal.provider, "not provider");
        require(deal.state == State.Proposed, "wrong state");
        require(msg.value == deal.collateral, "wrong payment");
        deal.state = State.Active;
        deal.startBlock = uint64(block.number);
    }

    // Bob sends the asked chunks and their Merkle paths. If all are correct, he is paid.
    function submitProof(uint256 dealId, bytes[] calldata chunks, bytes32[][] calldata proofs)
        external
        nonReentrant
    {
        Deal storage deal = deals[dealId];
        require(msg.sender == deal.provider, "not provider");
        require(deal.state == State.Active, "wrong state");
        uint32 period = deal.nextPeriod;

        (, uint256 deadline) = periodWindow(dealId, period);
        require(block.number <= deadline, "too late");

        // the contract picks the chunks itself, Bob cannot choose them
        uint256[] memory asked = getChallenges(dealId, period);
        require(chunks.length == asked.length && proofs.length == asked.length, "wrong number of proofs");
        for (uint256 j = 0; j < asked.length; j++) {
            require(proofs[j].length == deal.depth, "wrong proof length");
            require(verifyChunk(deal.root, asked[j], chunks[j], proofs[j]), "invalid proof");
        }

        // update the deal first, then send the money
        deal.nextPeriod = period + 1;
        uint256 payment = deal.pricePerPeriod;
        if (deal.nextPeriod == deal.numPeriods) {
            deal.state = State.Completed;
            payment = payment + deal.collateral; // last period: Bob gets his deposit back
        }
        sendEth(deal.provider, payment);
    }

    // After Bob misses a deadline, anyone can call this. The money always goes to Alice:
    // Bob's deposit + the periods that were not paid yet.
    function claimFailure(uint256 dealId) external nonReentrant {
        Deal storage deal = deals[dealId];
        require(deal.state == State.Active, "wrong state");
        (, uint256 deadline) = periodWindow(dealId, deal.nextPeriod);
        require(block.number > deadline, "too early");

        uint256 unpaid = deal.numPeriods - deal.nextPeriod;
        uint256 amount = deal.collateral + deal.pricePerPeriod * unpaid;
        deal.state = State.Failed;
        sendEth(deal.user, amount);
    }

    function getDeal(uint256 dealId) external view returns (Deal memory) {
        return deals[dealId];
    }

    // When a period starts (seed block) and its deadline.
    // Bob can answer in the blocks after the seed block, up to the deadline.
    function periodWindow(uint256 dealId, uint32 period) public view returns (uint256 seedBlock, uint256 deadline) {
        Deal storage deal = deals[dealId];
        seedBlock = uint256(deal.startBlock) + uint256(period) * deal.periodBlocks;
        deadline = seedBlock + deal.periodBlocks;
    }

    // The random chunk numbers Bob must prove in this period.
    function getChallenges(uint256 dealId, uint32 period) public view returns (uint256[] memory asked) {
        Deal storage deal = deals[dealId];
        require(deal.state == State.Active, "wrong state");
        (uint256 seedBlock,) = periodWindow(dealId, period);

        // the hash of the seed block is the random seed; nobody knows it before the block exists
        require(block.number > seedBlock, "too early");
        bytes32 seed = blockhash(seedBlock);
        require(seed != bytes32(0), "seed too old");

        asked = new uint256[](deal.challengesPerPeriod);
        for (uint256 j = 0; j < asked.length; j++) {
            // only real chunks are picked, never the zero padding
            asked[j] = uint256(keccak256(abi.encode(seed, dealId, period, j))) % deal.numChunks;
        }
    }

    // Hash of one chunk. Same as leaf() in merkle.py.
    // 0 marks a leaf, and the index ties the chunk to its position.
    function leafHash(uint256 index, bytes calldata chunk) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(uint8(0), index, chunk));
    }

    // Hash of two children. Same as node() in merkle.py. 1 marks an inner node.
    function nodeHash(bytes32 left, bytes32 right) internal pure returns (bytes32) {
        return keccak256(abi.encodePacked(uint8(1), left, right));
    }

    // Checks that the chunk and its path lead up to the root. Same as verify() in merkle.py.
    // i & 1 means i % 2 and i >> 1 means i / 2; they do the same but cost less gas.
    function verifyChunk(bytes32 root, uint256 index, bytes calldata chunk, bytes32[] calldata proof)
        public
        pure
        returns (bool)
    {
        bytes32 h = leafHash(index, chunk);
        uint256 i = index;
        for (uint256 k = 0; k < proof.length; k++) {
            if (i & 1 == 0) {
                h = nodeHash(h, proof[k]);   // we are the left child
            } else {
                h = nodeHash(proof[k], h);   // we are the right child
            }
            i = i >> 1;
        }
        return i == 0 && h == root;
    }

    // How many levels the tree has: the smallest depth with 2^depth >= n.
    function treeDepth(uint64 n) internal pure returns (uint8 depth) {
        while ((uint256(1) << depth) < n) {
            depth++;
        }
    }

    function sendEth(address to, uint256 amount) internal {
        (bool ok,) = to.call{value: amount}("");
        require(ok, "transfer failed");
    }
}
