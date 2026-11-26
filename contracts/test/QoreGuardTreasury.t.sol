// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {QoreGuardTreasury} from "../src/QoreGuardTreasury.sol";
import {MockPQCVerify, MockPQCKeys} from "../src/mocks/MockPQC.sol";

/// @notice Unit tests for the policy logic, with the two QoreChain precompiles
///         replaced by mock contracts etched at their real addresses.
contract QoreGuardTreasuryTest is Test {
    address internal constant PQC_VERIFY = 0x0000000000000000000000000000000000000a01;
    address internal constant PQC_KEYS = 0x0000000000000000000000000000000000000a02;

    QoreGuardTreasury internal treasury;
    MockPQCKeys internal keys;

    address internal owner = address(0xA11CE);
    address internal agent = address(0xB0B);
    address internal attacker = address(0xBAD);
    address internal hosting = address(0x1111);
    address internal apiVendor = address(0x2222);

    bytes internal ownerPk = hex"deadbeefdeadbeef";
    bytes internal badSig = hex"00";

    uint256 internal constant WEI = 1e12; // wei per uqor on the EVM lane

    uint96 internal constant TREASURY_DAILY_CAP = 1_200_000; // 1.2 QOR
    uint96 internal constant HOSTING_TX_CAP = 200_000; // 0.2 QOR
    uint96 internal constant HOSTING_DAY_CAP = 1_000_000; // 1.0 QOR
    uint96 internal constant API_TX_CAP = 300_000;
    uint96 internal constant API_DAY_CAP = 600_000;

    function setUp() public {
        vm.etch(PQC_VERIFY, address(new MockPQCVerify()).code);
        vm.etch(PQC_KEYS, address(new MockPQCKeys()).code);
        keys = MockPQCKeys(PQC_KEYS);

        treasury = new QoreGuardTreasury(owner, TREASURY_DAILY_CAP);
        vm.deal(owner, 100 ether);
        vm.deal(address(this), 100 ether);
        vm.deal(agent, 1 ether);

        keys.setKey(owner, 1, ownerPk, true);
        _addVendor(1, hosting, "cloud-hosting", HOSTING_TX_CAP, HOSTING_DAY_CAP);
        _addVendor(2, apiVendor, "api-credits", API_TX_CAP, API_DAY_CAP);
    }

    // ------------------------------------------------------------------ //
    // helpers
    // ------------------------------------------------------------------ //

    function _sign(bytes memory message) internal pure returns (bytes memory) {
        return abi.encodePacked(bytes1(0x01), message);
    }

    function _addVendor(uint256 id, address payee, string memory label, uint96 txCap, uint96 dayCap) internal {
        bytes32 payload = keccak256(abi.encode(id, payee, keccak256(bytes(label)), txCap, dayCap));
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.adminMessage(treasury.ADD_VENDOR(), payload, nonce);
        vm.prank(owner);
        treasury.addVendor(id, payee, keccak256(bytes(label)), txCap, dayCap, nonce, ownerPk, _sign(message));
    }

    function _pay(uint256 vendorId, uint256 amountUqor, bytes32 memo) internal {
        vm.prank(owner);
        treasury.pay{value: amountUqor * WEI}(vendorId, amountUqor, memo);
    }

    // ------------------------------------------------------------------ //
    // happy path
    // ------------------------------------------------------------------ //

    function testVendorsRegisteredByOwnerSignature() public {
        (address payee, bytes32 label, uint96 txCap, uint96 dayCap, bool active) = treasury.vendor(1);
        assertEq(payee, hosting);
        assertEq(label, keccak256(bytes("cloud-hosting")));
        assertEq(txCap, HOSTING_TX_CAP);
        assertEq(dayCap, HOSTING_DAY_CAP);
        assertTrue(active);
        assertEq(treasury.vendorCount(), 2);
        assertEq(treasury.approvalNonce(), 2); // one per signed action
    }

    function testPayWithinCapsForwardsValue() public {
        uint256 before = hosting.balance;
        _pay(1, 150_000, keccak256("INV-1001"));
        assertEq(hosting.balance - before, 150_000 * WEI);
        assertEq(treasury.totalSpentUqor(), 150_000);
        assertEq(treasury.treasurySpentToday(), 150_000);
        assertEq(treasury.spentToday(1), 150_000);
    }

    // ------------------------------------------------------------------ //
    // refusals: contract-level policy
    // ------------------------------------------------------------------ //

    function testOnlyOwnerAccountCanPay() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.NotOwnerAccount.selector, agent));
        treasury.pay{value: WEI}(1, 1, bytes32(0));
    }

    function testPerVendorTxCapRefuses() public {
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.PerVendorTxCapExceeded.selector, 250_000, HOSTING_TX_CAP
            )
        );
        treasury.pay{value: 250_000 * WEI}(1, 250_000, keccak256("INV-1002"));
    }

    function testPerVendorDayCapRefuses() public {
        _pay(1, 200_000, keccak256("a"));
        _pay(1, 200_000, keccak256("b"));
        _pay(1, 200_000, keccak256("c"));
        _pay(1, 200_000, keccak256("d"));
        _pay(1, 200_000, keccak256("e")); // 1.0 QOR = the vendor daily cap
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.PerVendorDayCapExceeded.selector, 1_100_000, HOSTING_DAY_CAP
            )
        );
        treasury.pay{value: 100_000 * WEI}(1, 100_000, keccak256("f"));
    }

    function testTreasuryDayCapRefuses() public {
        _pay(1, 200_000, keccak256("a"));
        _pay(2, 300_000, keccak256("b"));
        _pay(2, 300_000, keccak256("c"));
        _pay(1, 200_000, keccak256("d"));
        _pay(1, 200_000, keccak256("e")); // 1.2 QOR in total
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.TreasuryDayCapExceeded.selector, 1_300_000, TREASURY_DAILY_CAP
            )
        );
        treasury.pay{value: 100_000 * WEI}(1, 100_000, keccak256("f"));
    }

    function testInactiveVendorRefused() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint256(2), false));
        bytes memory message = treasury.adminMessage(treasury.SET_VENDOR_ACTIVE(), payload, nonce);
        vm.prank(owner);
        treasury.setVendorActive(2, false, nonce, ownerPk, _sign(message));

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.VendorInactive.selector, 2));
        treasury.pay{value: WEI}(2, 1, bytes32(0));
    }

    function testUnknownVendorRefused() public {
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.UnknownVendor.selector, 99));
        treasury.pay{value: WEI}(99, 1, bytes32(0));
    }

    function testValueMustMatchAmount() public {
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.ValueMismatch.selector, 1000, 150_000 * WEI
            )
        );
        treasury.pay{value: 1000}(1, 150_000, bytes32(0));
    }

    // ------------------------------------------------------------------ //
    // refusals: post-quantum gate on policy changes
    // ------------------------------------------------------------------ //

    function testAdminWithoutPqcKeyRegisteredRefused() public {
        keys.clearKey(owner);
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.PqcKeyNotRegistered.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    function testAdminWithUnprovenKeyRefused() public {
        keys.setKey(owner, 1, ownerPk, false);
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.PqcKeyNotProven.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    function testAdminWithWrongKeyRefused() public {
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.PqcKeyMismatch.selector);
        treasury.setTreasuryCap(5, nonce, hex"cafebabe", _sign(message));
    }

    function testAdminWithBadSignatureRefused() public {
        uint256 nonce = treasury.approvalNonce();
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, badSig);
    }

    function testAdminSignatureIsReplayBoundedByNonce() public {
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
        vm.expectRevert(
            abi.encodeWithSelector(QoreGuardTreasury.StaleApprovalNonce.selector, nonce, nonce + 1)
        );
        treasury.setTreasuryCap(9, nonce, ownerPk, _sign(message));
    }

    /// A classical key alone (no ML-DSA-87 signature) cannot change the policy.
    function testStolenClassicalKeyCannotChangePolicy() public {
        uint256 nonce = treasury.approvalNonce();
        vm.prank(attacker);
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.setTreasuryCap(999_999_999, nonce, ownerPk, hex"");
    }

    // ------------------------------------------------------------------ //
    // exception path
    // ------------------------------------------------------------------ //

    function testPayWithApprovalAboveVendorCap() public {
        uint256 nonce = treasury.approvalNonce();
        uint256 amount = 500_000; // above the 0.2 QOR vendor per-tx cap
        bytes32 memo = keccak256("INV-1002 emergency");
        bytes memory message = treasury.exceptionMessage(1, amount, memo, nonce);

        // Without a signature the same call is refused.
        vm.prank(owner);
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.payWithApproval{value: amount * WEI}(1, amount, memo, nonce, ownerPk, badSig);

        uint256 before = hosting.balance;
        vm.prank(owner);
        treasury.payWithApproval{value: amount * WEI}(
            1, amount, memo, nonce, ownerPk, _sign(message)
        );
        assertEq(hosting.balance - before, amount * WEI);
        assertEq(treasury.approvalNonce(), nonce + 1);
    }

    function testApprovalForOneAmountCannotPayAnother() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("INV-1002");
        bytes memory message = treasury.exceptionMessage(1, 500_000, memo, nonce);
        // The owner approved 0.5 QOR; the agent submits 0.9 QOR with that signature.
        vm.prank(owner);
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.payWithApproval{value: 900_000 * WEI}(
            1, 900_000, memo, nonce, ownerPk, _sign(message)
        );
    }

    function testTreasuryCapStillAppliesToApprovedPayment() public {
        _pay(1, 200_000, keccak256("a"));
        _pay(2, 300_000, keccak256("b"));
        _pay(2, 300_000, keccak256("c"));
        _pay(1, 200_000, keccak256("d"));
        _pay(1, 200_000, keccak256("e")); // 1.2 QOR = treasury cap
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("f");
        bytes memory message = treasury.exceptionMessage(1, 100_000, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.TreasuryDayCapExceeded.selector, 1_300_000, TREASURY_DAILY_CAP
            )
        );
        treasury.payWithApproval{value: 100_000 * WEI}(
            1, 100_000, memo, nonce, ownerPk, _sign(message)
        );
    }

    // ------------------------------------------------------------------ //
    // precompile hygiene
    // ------------------------------------------------------------------ //

    /// An inactive precompile answers success with empty data. The contract
    /// must refuse that instead of reading it as "registered".
    function testEmptyPrecompileAnswerRefused() public {
        uint256 nonce = treasury.approvalNonce();
        vm.etch(PQC_KEYS, hex"");
        vm.expectRevert(QoreGuardTreasury.PrecompileUnavailable.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, badSig);
    }

    function testReceiveRefused() public {
        vm.expectRevert();
        payable(address(treasury)).transfer(1);
    }
}
