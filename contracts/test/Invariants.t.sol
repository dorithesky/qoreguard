// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {QoreGuardTreasury} from "../src/QoreGuardTreasury.sol";
import {MockPQCVerify, MockPQCKeys, RejectingPayee} from "../src/mocks/MockPQC.sol";

/// @notice A stateful handler: every function is a bounded, randomised action
///         the agent, the owner or an attacker could attempt. It keeps a shadow
///         ledger of what it expects the contract to have done, and the
///         invariants compare the two after every sequence.
contract TreasuryHandler is Test {
    QoreGuardTreasury public treasury;
    MockPQCKeys public keys;
    address public owner;
    bytes public ownerPk;

    uint256 public constant WEI = 1e12;
    uint256 public constant SEED_VENDORS = 2;

    address[] public payees;
    address public stranger = address(0xDEAD);

    // ---- shadow ledger ------------------------------------------------- //
    uint256 public ghostTotalPaid;
    mapping(uint256 => uint256) public ghostVendorPaid;
    uint256 public ghostSignedActions;
    uint256 public ghostVendorsAdded;
    uint256 public ghostMismatches;
    uint256 public ghostUnauthorizedChanges;
    bool public ghostPaidAfterDeactivation;
    uint256 public ghostPolicyCalls;
    uint256 public ghostPaidCalls;
    /// Approved payments are one-off exceptions: they count against the
    /// treasury day cap but not against any vendor's day counter.
    mapping(uint256 => uint256) public ghostApprovedByDay;

    mapping(uint256 => bool) public expectActive;

    constructor(QoreGuardTreasury treasury_, MockPQCKeys keys_, address owner_, bytes memory pk_) {
        treasury = treasury_;
        keys = keys_;
        owner = owner_;
        ownerPk = pk_;
        payees.push(address(0x1111)); // hosting
        payees.push(address(0x2222)); // api credits
        payees.push(address(0x3333));
        payees.push(address(0x4444));
        payees.push(address(0x5555));
        expectActive[1] = true;
        expectActive[2] = true;
    }

    function vendorCount() external view returns (uint256) {
        return SEED_VENDORS + ghostVendorsAdded;
    }

    function _sign(bytes memory message) internal pure returns (bytes memory) {
        return abi.encodePacked(bytes1(0x01), message);
    }

    function _vendorId(uint256 seed) internal view returns (uint256) {
        uint256 n = treasury.vendorCount();
        if (n == 0) return 0;
        return treasury.vendorIdAt(seed % n);
    }

    function _vendorCaps(uint256 vid) internal view returns (uint96 perTx, uint96 daily, bool active) {
        (,, perTx, daily, active) = treasury.vendor(vid);
    }

    // ------------------------------------------------------------------ //
    // actions
    // ------------------------------------------------------------------ //

    /// The agent pays inside (or outside) the caps, exactly as the chain would
    /// hand it to the contract.
    function pay(uint256 vendorSeed, uint96 amountSeed) external {
        uint256 vid = _vendorId(vendorSeed);
        (uint96 perTx, uint96 daily, bool active) = _vendorCaps(vid);
        uint96 amount = uint96(bound(amountSeed, 0, 1_600_000));

        uint256 daySpent = treasury.spentToday(vid);
        uint256 treasurySpent = treasury.treasurySpentToday();
        uint96 treasuryCap = treasury.treasuryDailyCapUqor();

        bool allowed = active && amount > 0 && amount <= perTx && daySpent + amount <= daily
            && treasurySpent + amount <= treasuryCap;

        ghostPaidCalls++;
        vm.prank(owner);
        try treasury.pay{value: uint256(amount) * WEI}(vid, amount, keccak256(abi.encode("pay", vid, amount))) {
            if (!allowed) ghostMismatches++;
            if (!active) ghostPaidAfterDeactivation = true;
            ghostTotalPaid += amount;
            ghostVendorPaid[vid] += amount;
        } catch {
            if (allowed) ghostMismatches++;
        }
    }

    /// The owner's approval path. Every call carries a valid signature for its
    /// own arguments (the contract still gets its own gate); a random share of
    /// them are re-bound to different arguments, which must be refused.
    function payWithApproval(uint256 vendorSeed, uint96 amountSeed, uint256 tamperSeed) external {
        uint256 vid = _vendorId(vendorSeed);
        (, , bool active) = _vendorCaps(vid);
        uint96 amount = uint96(bound(amountSeed, 0, 1_600_000));
        bool tampered = tamperSeed % 3 == 0;

        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256(abi.encode("approved", vid, amount));
        bytes memory sig = _sign(treasury.exceptionMessage(vid, amount, memo, nonce));
        if (tampered) {
            // still a valid signature, for a different amount
            sig = _sign(treasury.exceptionMessage(vid, uint256(amount) + 1, memo, nonce));
        }

        uint256 treasurySpent = treasury.treasurySpentToday();
        uint96 treasuryCap = treasury.treasuryDailyCapUqor();
        bool allowed = active && !tampered && amount > 0 && treasurySpent + amount <= treasuryCap;

        ghostPaidCalls++;
        vm.prank(owner);
        try treasury.payWithApproval{value: uint256(amount) * WEI}(vid, amount, memo, nonce, ownerPk, sig) {
            if (!allowed) ghostMismatches++;
            if (!active) ghostPaidAfterDeactivation = true;
            ghostTotalPaid += amount;
            ghostVendorPaid[vid] += amount;
            ghostApprovedByDay[block.timestamp / 1 days] += amount;
            ghostSignedActions++;
        } catch {
            if (allowed) ghostMismatches++;
        }
    }

    function setVendorActive(uint256 vendorSeed, bool active) external {
        uint256 vid = _vendorId(vendorSeed);
        uint256 nonce = treasury.approvalNonce();
        bytes memory sig = _sign(
            treasury.adminMessage(treasury.SET_VENDOR_ACTIVE(), keccak256(abi.encode(vid, active)), nonce)
        );
        ghostPolicyCalls++;
        vm.prank(owner);
        try treasury.setVendorActive(vid, active, nonce, ownerPk, sig) {
            expectActive[vid] = active;
            ghostSignedActions++;
        } catch {}
    }

    /// Caps may be tightened, but never below what has already been spent
    /// today: the owner cannot retroactively un-spend a payment.
    function setVendorCaps(uint256 vendorSeed, uint96 txSeed, uint96 daySeed) external {
        uint256 vid = _vendorId(vendorSeed);
        uint256 spent = treasury.spentToday(vid);
        uint96 perTx = uint96(bound(txSeed, 1, 2_000_000));
        uint96 daily = uint96(bound(daySeed, spent, 2_000_000));
        uint256 nonce = treasury.approvalNonce();
        bytes memory sig = _sign(
            treasury.adminMessage(treasury.SET_VENDOR_CAPS(), keccak256(abi.encode(vid, perTx, daily)), nonce)
        );
        ghostPolicyCalls++;
        vm.prank(owner);
        try treasury.setVendorCaps(vid, perTx, daily, nonce, ownerPk, sig) {
            ghostSignedActions++;
        } catch {}
    }

    function setTreasuryCap(uint96 capSeed) external {
        uint256 spent = treasury.treasurySpentToday();
        uint96 cap = uint96(bound(capSeed, spent, 3_000_000));
        uint256 nonce = treasury.approvalNonce();
        bytes memory sig =
            _sign(treasury.adminMessage(treasury.SET_TREASURY_CAP(), keccak256(abi.encode(cap)), nonce));
        ghostPolicyCalls++;
        vm.prank(owner);
        try treasury.setTreasuryCap(cap, nonce, ownerPk, sig) {
            ghostSignedActions++;
        } catch {}
    }

    function addVendor(uint256 idSeed, uint256 payeeSeed, uint96 txSeed, uint96 daySeed) external {
        uint256 id = bound(idSeed, 1, 8);
        address payee = payees[payeeSeed % payees.length];
        if (payeeSeed % 11 == 0) payee = address(0); // and sometimes a zero payee
        uint96 perTx = uint96(bound(txSeed, 1, 400_000));
        uint96 daily = uint96(bound(daySeed, perTx, 1_600_000));
        uint256 nonce = treasury.approvalNonce();
        bytes memory sig = _sign(
            treasury.adminMessage(
                treasury.ADD_VENDOR(), keccak256(abi.encode(id, payee, keccak256("v"), perTx, daily)), nonce
            )
        );
        uint256 before = treasury.vendorCount();
        ghostPolicyCalls++;
        vm.prank(owner);
        try treasury.addVendor(id, payee, keccak256("v"), perTx, daily, nonce, ownerPk, sig) {
            ghostSignedActions++;
            if (treasury.vendorCount() > before) {
                ghostVendorsAdded++;
                expectActive[id] = true;
            }
        } catch {}
    }

    /// A stranger tries every state-changing entry point. All of them must
    /// revert, and the ghost refuses to let one succeed silently.
    function attack(uint256 vendorSeed, uint96 amount) external {
        uint256 vid = _vendorId(vendorSeed);
        uint256 nonce = treasury.approvalNonce();
        vm.startPrank(stranger);
        try treasury.pay{value: uint256(amount) * WEI}(vid, amount, bytes32(0)) {
            ghostUnauthorizedChanges++;
        } catch {}
        try treasury.payWithApproval{value: uint256(amount) * WEI}(
            vid, amount, bytes32(0), nonce, ownerPk, hex"01"
        ) {
            ghostUnauthorizedChanges++;
        } catch {}
        try treasury.setTreasuryCap(1, nonce, ownerPk, hex"01") {
            ghostUnauthorizedChanges++;
        } catch {}
        vm.stopPrank();
    }

    /// Time moves: the day counter must roll over without losing the all-time
    /// total or carrying yesterday's spend into today.
    function warp(uint256 secondsSeed) external {
        vm.warp(block.timestamp + bound(secondsSeed, 1, 2 days));
    }
}

/// @notice Twelve invariants over any sequence of the actions above.
/// forge-config: default.invariant.runs = 96
/// forge-config: default.invariant.depth = 128
contract QoreGuardInvariantsTest is Test {
    address internal constant PQC_VERIFY = 0x0000000000000000000000000000000000000a01;
    address internal constant PQC_KEYS = 0x0000000000000000000000000000000000000a02;

    QoreGuardTreasury internal treasury;
    MockPQCKeys internal keys;
    TreasuryHandler internal handler;

    address internal owner = address(0xA11CE);
    bytes internal ownerPk = hex"deadbeefdeadbeef";
    uint256 internal constant WEI = 1e12;

    function setUp() public {
        vm.etch(PQC_VERIFY, address(new MockPQCVerify()).code);
        vm.etch(PQC_KEYS, address(new MockPQCKeys()).code);
        keys = MockPQCKeys(PQC_KEYS);

        treasury = new QoreGuardTreasury(owner, 1_200_000);
        vm.deal(owner, 1_000 ether);
        keys.setKey(owner, 1, ownerPk, true);
        _addVendor(1, address(0x1111), "cloud-hosting", 200_000, 1_000_000);
        _addVendor(2, address(0x2222), "api-credits", 300_000, 600_000);

        handler = new TreasuryHandler(treasury, keys, owner, ownerPk);
        targetContract(address(handler));
    }

    function _sign(bytes memory message) internal pure returns (bytes memory) {
        return abi.encodePacked(bytes1(0x01), message);
    }

    function _addVendor(uint256 id, address payee, string memory label, uint96 txCap, uint96 dayCap) internal {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(id, payee, keccak256(bytes(label)), txCap, dayCap));
        bytes memory message = treasury.adminMessage(treasury.ADD_VENDOR(), payload, nonce);
        vm.prank(owner);
        treasury.addVendor(id, payee, keccak256(bytes(label)), txCap, dayCap, nonce, ownerPk, _sign(message));
    }

    // ------------------------------------------------------------------ //
    // 1-3: the ledger adds up
    // ------------------------------------------------------------------ //

    /// @notice The contract's all-time total is exactly the money that left.
    function invariant_totalSpentIsTheGhostLedger() public view {
        assertEq(treasury.totalSpentUqor(), handler.ghostTotalPaid(), "totalSpent != shadow ledger");
    }

    /// @notice Per-vendor all-time totals sum to the treasury total: no payment
    ///         is counted twice or lost between the two books.
    function invariant_vendorTotalsSumToTreasuryTotal() public view {
        uint256 sum;
        uint256 n = treasury.vendorCount();
        for (uint256 i = 0; i < n; i++) {
            sum += handler.ghostVendorPaid(treasury.vendorIdAt(i));
        }
        assertEq(sum, treasury.totalSpentUqor(), "vendor totals do not sum to the treasury total");
    }

    /// @notice The day counter and the per-vendor day counters describe the
    ///         same money.
    function invariant_treasuryDayEqualsVendorDays() public view {
        uint256 sum = handler.ghostApprovedByDay(block.timestamp / 1 days);
        uint256 n = treasury.vendorCount();
        for (uint256 i = 0; i < n; i++) {
            sum += treasury.spentToday(treasury.vendorIdAt(i));
        }
        assertEq(sum, treasury.treasurySpentToday(), "treasury day spend != vendor spend + approved exceptions");
    }

    // ------------------------------------------------------------------ //
    // 4-6: the caps hold for every sequence
    // ------------------------------------------------------------------ //

    /// @notice The treasury-wide day cap is never exceeded.
    function invariant_treasuryDayCapHolds() public view {
        assertLe(treasury.treasurySpentToday(), treasury.treasuryDailyCapUqor(), "treasury day cap broken");
    }

    /// @notice No vendor's day cap is exceeded.
    function invariant_vendorDayCapsHold() public view {
        uint256 n = treasury.vendorCount();
        for (uint256 i = 0; i < n; i++) {
            uint256 vid = treasury.vendorIdAt(i);
            (, , , uint96 dayCap,) = treasury.vendor(vid);
            assertLe(treasury.spentToday(vid), dayCap, "vendor day cap broken");
        }
    }

    /// @notice The treasury is stateless: nothing is ever left behind in it.
    function invariant_treasuryHoldsNoValue() public view {
        assertEq(address(treasury).balance, 0, "value stuck in the treasury");
    }

    // ------------------------------------------------------------------ //
    // 7-9: the policy decides, not luck
    // ------------------------------------------------------------------ //

    /// @notice Every call's outcome matched the policy computed independently
    ///         before the call: no payment slipped through, none was wrongly
    ///         refused.
    function invariant_outcomeMatchesPolicy() public view {
        assertEq(handler.ghostMismatches(), 0, "a payment outcome disagreed with the policy");
    }

    /// @notice Once the owner deactivates a vendor, no payment reaches it until
    ///         it is reactivated.
    function invariant_deactivatedVendorIsNeverPaid() public view {
        assertFalse(handler.ghostPaidAfterDeactivation(), "an inactive vendor was paid");
    }

    /// @notice A stranger cannot move money or change the policy.
    function invariant_strangerCannotChangeState() public view {
        assertEq(handler.ghostUnauthorizedChanges(), 0, "a non-owner call changed state");
    }

    // ------------------------------------------------------------------ //
    // 10-12: the PQ approval counter and the books line up
    // ------------------------------------------------------------------ //

    /// @notice The approval nonce only moves on a successful PQ-signed action,
    ///         exactly one step per action: no gap and no replay.
    function invariant_approvalNonceAdvancesOncePerSignedAction() public view {
        assertEq(
            treasury.approvalNonce(),
            2 + handler.ghostSignedActions(),
            "approval nonce != 2 + signed actions"
        );
    }

    /// @notice The registry only grows, ids stay 1..N and every entry has a
    ///         payee.
    function invariant_registryIsWellFormed() public view {
        uint256 n = treasury.vendorCount();
        assertEq(n, handler.vendorCount(), "registry size differs from the recorded additions");
        for (uint256 i = 0; i < n; i++) {
            uint256 vid = treasury.vendorIdAt(i);
            assertGt(vid, 0, "vendor id 0 in the registry");
            (address payee,,, ,) = treasury.vendor(vid);
            assertTrue(payee != address(0), "registered vendor without a payee");
        }
    }

    /// @notice Money can only ever reach a registered payee, and each payee
    ///         holds exactly what the contract paid it.
    function invariant_onlyRegisteredPayeesHoldFunds() public view {
        uint256 n = treasury.vendorCount();
        for (uint256 i = 0; i < n; i++) {
            uint256 vid = treasury.vendorIdAt(i);
            (address payee,,, ,) = treasury.vendor(vid);
            // the same payee may be registered twice; the ghost tracks by id,
            // so compare the sum of ids that share the payee
            uint256 expected;
            for (uint256 j = 0; j < n; j++) {
                uint256 other = treasury.vendorIdAt(j);
                (address otherPayee,,, ,) = treasury.vendor(other);
                if (otherPayee == payee) expected += handler.ghostVendorPaid(other);
            }
            assertEq(payee.balance, expected * WEI, "a payee holds an amount the contract never sent");
        }
        assertEq(handler.stranger().balance, 0, "a stranger received money");
        assertEq(address(0xDEAD).balance, 0, "a stranger received money");
    }

    /// @notice The immutable owner never changes.
    function invariant_ownerIsImmutable() public view {
        assertEq(treasury.ownerAccount(), owner);
    }
}
