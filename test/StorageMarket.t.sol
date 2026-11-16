// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {StorageMarket} from "../src/StorageMarket.sol";

// An attacker that tries to get its refund twice: while receiving the refund,
// it calls cancelDeal again.
contract Attacker {
    StorageMarket market;
    uint256 dealId;
    bytes public error;

    constructor(StorageMarket m) {
        market = m;
    }

    function openDeal(bytes32 root, uint64 numChunks, address bob) external payable {
        dealId = market.createDeal{value: msg.value}(root, numChunks, 1, 10, 1, msg.value, bob, 0);
    }

    function cancel() external {
        market.cancelDeal(dealId);
    }

    // runs when the refund arrives
    receive() external payable {
        try market.cancelDeal(dealId) {} catch (bytes memory e) {
            error = e;
        }
    }
}

contract StorageMarketTest is Test {
    StorageMarket market;

    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    address stranger = makeAddr("stranger");

    // a small test file (5 chunks), made by offchain/merkle.py
    bytes32 root;
    uint64 numChunks;
    bytes[] chunks;
    bytes32[][] proofs;

    uint32 constant PERIODS = 3;
    uint32 constant PERIOD_BLOCKS = 10;
    uint8 constant CHECKS = 4;
    uint256 constant PRICE = 0.01 ether;
    uint256 constant DEPOSIT = 0.05 ether;

    function setUp() public {
        market = new StorageMarket();

        // read the test tree written by merkle.py
        string memory json = vm.readFile("test/vectors.json");
        root = vm.parseJsonBytes32(json, ".root");
        numChunks = uint64(vm.parseJsonUint(json, ".numChunks"));
        bytes[] memory c = vm.parseJsonBytesArray(json, ".chunks");
        for (uint256 i = 0; i < c.length; i++) {
            chunks.push(c[i]);
            proofs.push(vm.parseJsonBytes32Array(json, string.concat(".proofs[", vm.toString(i), "]")));
        }

        vm.deal(alice, 10 ether);
        vm.deal(bob, 10 ether);
        vm.deal(stranger, 10 ether);
        vm.roll(1000); // start at block 1000
    }

    // ---------- helpers ----------

    // Alice opens a deal
    function openDeal() internal returns (uint256 id) {
        vm.prank(alice);
        id = market.createDeal{value: PRICE * PERIODS}(root, numChunks, PERIODS, PERIOD_BLOCKS, CHECKS, PRICE, bob, DEPOSIT);
    }

    // Alice opens a deal and Bob accepts it
    function openAndAccept() internal returns (uint256 id) {
        id = openDeal();
        vm.prank(bob);
        market.acceptDeal{value: DEPOSIT}(id);
    }

    // jump to the first block where Bob can answer this period
    function goToPeriod(uint256 id, uint32 period) internal {
        (uint256 seedBlock,) = market.periodWindow(id, period);
        vm.roll(seedBlock + 1);
        vm.setBlockhash(seedBlock, keccak256(abi.encode("seed", seedBlock)));
    }

    // Bob's honest answer: exactly the chunks the contract asks for
    function honestAnswer(uint256 id, uint32 period) internal view returns (bytes[] memory c, bytes32[][] memory p) {
        uint256[] memory asked = market.getChallenges(id, period);
        c = new bytes[](asked.length);
        p = new bytes32[][](asked.length);
        for (uint256 j = 0; j < asked.length; j++) {
            c[j] = chunks[asked[j]];
            p[j] = proofs[asked[j]];
        }
    }

    // Bob sends an answer
    function submit(uint256 id, bytes[] memory c, bytes32[][] memory p) internal {
        vm.prank(bob);
        market.submitProof(id, c, p);
    }

    function stateOf(uint256 id) internal view returns (StorageMarket.State) {
        return market.getDeal(id).state;
    }

    // ---------- Merkle tree ----------

    // the proofs made by Python are accepted by the contract
    function test_MerkleVectorsFromPython() public view {
        for (uint256 i = 0; i < chunks.length; i++) {
            assertTrue(market.verifyChunk(root, i, chunks[i], proofs[i]));
        }
    }

    // any change makes the proof fail
    function test_MerkleTamperedFails() public view {
        bytes memory c = chunks[2];
        c[0] = bytes1(uint8(c[0]) ^ 1);
        assertFalse(market.verifyChunk(root, 2, c, proofs[2]));          // changed chunk

        bytes32[] memory p = proofs[2];
        p[1] = bytes32(uint256(p[1]) ^ 1);
        assertFalse(market.verifyChunk(root, 2, chunks[2], p));          // changed sibling hash

        assertFalse(market.verifyChunk(root, 3, chunks[2], proofs[2]));  // wrong position
        assertFalse(market.verifyChunk(root, 10, chunks[2], proofs[2])); // position outside the tree
    }

    // ---------- honest deal ----------

    // Bob proves all 3 periods: he earns 3 x 0.01 and gets his deposit back
    function test_HappyPath() public {
        uint256 aliceBefore = alice.balance;
        uint256 bobBefore = bob.balance;
        uint256 id = openAndAccept();
        assertEq(address(market).balance, PRICE * PERIODS + DEPOSIT); // contract holds all the money

        for (uint32 period = 0; period < PERIODS; period++) {
            goToPeriod(id, period);
            (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, period);
            submit(id, c, p);
        }

        assertEq(uint8(stateOf(id)), uint8(StorageMarket.State.Completed));
        assertEq(alice.balance, aliceBefore - PRICE * PERIODS);
        assertEq(bob.balance, bobBefore + PRICE * PERIODS);
        assertEq(address(market).balance, 0); // nothing left in the contract
    }

    // every period: a bad proof pays nothing, a good proof pays Bob exactly 0.01
    // (and in the last period also gives his deposit back)
    function test_PaymentReleasedEachPeriod() public {
        uint256 id = openAndAccept();

        for (uint32 period = 0; period < PERIODS; period++) {
            goToPeriod(id, period);
            (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, period);

            // bad proof: one byte changed in a copy of the first chunk
            bytes[] memory bad = new bytes[](c.length);
            for (uint256 j = 0; j < c.length; j++) {
                bad[j] = c[j];
            }
            bad[0] = bytes.concat(c[0]);
            bad[0][0] = bytes1(uint8(bad[0][0]) ^ 1);

            uint256 bobBefore = bob.balance;
            uint256 marketBefore = address(market).balance;
            vm.expectRevert(bytes("invalid proof"));
            submit(id, bad, p);
            assertEq(bob.balance, bobBefore);                 // no money moved
            assertEq(address(market).balance, marketBefore);

            // good proof: Bob is paid for this period
            submit(id, c, p);
            uint256 expected = PRICE;
            if (period == PERIODS - 1) {
                expected = PRICE + DEPOSIT; // last period: deposit back too
            }
            assertEq(bob.balance, bobBefore + expected);
            assertEq(address(market).balance, marketBefore - expected);
        }
        assertEq(address(market).balance, 0);
    }

    // ---------- bad answers ----------

    // a changed chunk is rejected
    function test_RevertWrongChunk() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        c[0][5] = bytes1(uint8(c[0][5]) ^ 0xff);
        vm.expectRevert(bytes("invalid proof"));
        submit(id, c, p);
    }

    // a real chunk with its real proof, but not the one that was asked, is rejected
    function test_RevertRightChunkWrongIndex() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        uint256 asked = market.getChallenges(id, 0)[0];
        uint256 other = (asked + 1) % numChunks;
        c[0] = chunks[other];
        p[0] = proofs[other];
        vm.expectRevert(bytes("invalid proof"));
        submit(id, c, p);
    }

    // a proof with one hash missing is rejected
    function test_RevertTruncatedProof() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        bytes32[] memory shorter = new bytes32[](p[0].length - 1);
        for (uint256 i = 0; i < shorter.length; i++) {
            shorter[i] = p[0][i];
        }
        p[0] = shorter;
        vm.expectRevert(bytes("wrong proof length"));
        submit(id, c, p);
    }

    // a proof with one extra hash is rejected
    function test_RevertExtraProof() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        bytes32[] memory longer = new bytes32[](p[0].length + 1);
        for (uint256 i = 0; i < p[0].length; i++) {
            longer[i] = p[0][i];
        }
        p[0] = longer;
        vm.expectRevert(bytes("wrong proof length"));
        submit(id, c, p);
    }

    // 3 answers instead of 4 are rejected
    function test_RevertWrongProofCount() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        vm.expectRevert(bytes("wrong number of proofs"));
        submit(id, new bytes[](CHECKS - 1), new bytes32[][](CHECKS - 1));
    }

    // last period's answer does not work in the next period (new random chunks)
    function test_RevertProofForWrongPeriod() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        submit(id, c, p);

        goToPeriod(id, 1);
        vm.expectRevert();
        submit(id, c, p); // same answer again
    }

    // an answer after the deadline is rejected
    function test_RevertAfterDeadline() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        (, uint256 deadline) = market.periodWindow(id, 0);
        vm.roll(deadline + 1);
        vm.expectRevert(bytes("too late"));
        submit(id, c, p);
    }

    // an answer exactly at the deadline is still accepted
    function test_ProofAtDeadlineAccepted() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        (, uint256 deadline) = market.periodWindow(id, 0);
        vm.roll(deadline);
        submit(id, c, p);
    }

    // nobody can see the checks before the seed block exists
    function test_RevertBeforeSeedMined() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        submit(id, c, p);

        (uint256 seedBlock,) = market.periodWindow(id, 1);
        vm.roll(seedBlock); // the seed block is the current block, its hash is not known yet
        vm.expectRevert(bytes("too early"));
        market.getChallenges(id, 1);
        vm.expectRevert(bytes("too early"));
        submit(id, c, p);
    }

    // only Bob can send proofs
    function test_RevertNonProvider() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        vm.prank(stranger);
        vm.expectRevert(bytes("not provider"));
        market.submitProof(id, c, p);
    }

    // ---------- Bob fails ----------

    // Alice cannot claim before the deadline has passed
    function test_ClaimFailureTooEarly() public {
        uint256 id = openAndAccept();
        (, uint256 deadline) = market.periodWindow(id, 0);
        vm.roll(deadline);
        vm.expectRevert(bytes("too early"));
        market.claimFailure(id);
    }

    // Bob proves period 0, then stops: Alice gets 0.05 + 2 x 0.01
    function test_ClaimFailurePaysUser() public {
        uint256 id = openAndAccept();
        goToPeriod(id, 0);
        (bytes[] memory c, bytes32[][] memory p) = honestAnswer(id, 0);
        submit(id, c, p);

        uint256 aliceBefore = alice.balance;
        (, uint256 deadline) = market.periodWindow(id, 1);
        vm.roll(deadline + 1);
        vm.prank(stranger); // anyone can call it, but the money goes to Alice
        market.claimFailure(id);

        assertEq(alice.balance, aliceBefore + DEPOSIT + PRICE * (PERIODS - 1));
        assertEq(address(market).balance, 0);
        assertEq(uint8(stateOf(id)), uint8(StorageMarket.State.Failed));

        vm.expectRevert(bytes("wrong state"));
        market.claimFailure(id); // cannot claim twice
    }

    // ---------- cancel and accept ----------

    // Alice can cancel before Bob accepts and gets all her money back
    function test_CancelBeforeAccept() public {
        uint256 before = alice.balance;
        uint256 id = openDeal();

        vm.prank(stranger);
        vm.expectRevert(bytes("not user"));
        market.cancelDeal(id);

        vm.prank(alice);
        market.cancelDeal(id);
        assertEq(alice.balance, before);

        vm.prank(bob);
        vm.expectRevert(bytes("wrong state"));
        market.acceptDeal{value: DEPOSIT}(id); // too late for Bob now
    }

    // Alice cannot cancel after Bob accepted
    function test_CancelAfterAcceptReverts() public {
        uint256 id = openAndAccept();
        vm.prank(alice);
        vm.expectRevert(bytes("wrong state"));
        market.cancelDeal(id);
    }

    // only Bob can accept, and only with the exact deposit
    function test_AcceptChecks() public {
        uint256 id = openDeal();
        vm.prank(stranger);
        vm.expectRevert(bytes("not provider"));
        market.acceptDeal{value: DEPOSIT}(id);

        vm.prank(bob);
        vm.expectRevert(bytes("wrong payment"));
        market.acceptDeal{value: DEPOSIT - 1}(id);
    }

    // bad settings or a wrong payment are rejected
    function test_CreateDealValidation() public {
        vm.startPrank(alice);
        vm.expectRevert(bytes("wrong payment"));
        market.createDeal{value: 1}(root, numChunks, PERIODS, PERIOD_BLOCKS, CHECKS, PRICE, bob, DEPOSIT);
        vm.expectRevert(bytes("bad period length"));
        market.createDeal{value: PRICE * PERIODS}(root, numChunks, PERIODS, 256, CHECKS, PRICE, bob, DEPOSIT);
        vm.expectRevert(bytes("bad period length"));
        market.createDeal{value: PRICE * PERIODS}(root, numChunks, PERIODS, 1, CHECKS, PRICE, bob, DEPOSIT);
        vm.expectRevert(bytes("bad challenge count"));
        market.createDeal{value: PRICE * PERIODS}(root, numChunks, PERIODS, PERIOD_BLOCKS, 17, PRICE, bob, DEPOSIT);
        vm.expectRevert(bytes("bad params"));
        market.createDeal{value: PRICE * PERIODS}(root, 0, PERIODS, PERIOD_BLOCKS, CHECKS, PRICE, bob, DEPOSIT);
        vm.stopPrank();
    }

    // ---------- reentrancy ----------

    // the attacker tries to get its refund twice; it gets it only once
    function test_ReentrancyBlocked() public {
        Attacker attacker = new Attacker(market);
        vm.deal(address(this), 1 ether);
        attacker.openDeal{value: 1 ether}(root, numChunks, bob);
        openDeal(); // Alice's deal: money the attacker would like to steal
        uint256 marketBefore = address(market).balance;

        attacker.cancel();

        assertEq(attacker.error(), abi.encodeWithSignature("Error(string)", "reentrant call"));
        assertEq(address(attacker).balance, 1 ether);                 // refunded once
        assertEq(address(market).balance, marketBefore - 1 ether);    // Alice's money is still there
    }
}
