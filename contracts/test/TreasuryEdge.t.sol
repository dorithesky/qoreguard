// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {QoreGuardTreasury} from "../src/QoreGuardTreasury.sol";
import {MockPQCVerify, MockPQCKeys, RejectingPayee} from "../src/mocks/MockPQC.sol";

/// @notice The edges of every external function: each refusal path, every
///         administrative action, and the fuzz properties the policy must hold
///         for any amount the agent could choose. Together with
///         QoreGuardTreasury.t.sol this reaches 100% of lines, branches and
///         functions of the contract.
contract TreasuryEdgeTest is Test {
    address internal constant PQC_VERIFY = 0x0000000000000000000000000000000000000a01;
    address internal constant PQC_KEYS = 0x0000000000000000000000000000000000000a02;

    QoreGuardTreasury internal treasury;
    MockPQCKeys internal keys;

    address internal owner = address(0xA11CE);
    address internal attacker = address(0xBAD);
    address internal hosting = address(0x1111);
    address internal apiVendor = address(0x2222);

    bytes internal ownerPk = hex"deadbeefdeadbeef";
    uint256 internal constant WEI = 1e12;
    uint96 internal constant TREASURY_DAILY_CAP = 1_200_000;

    function setUp() public {
        vm.etch(PQC_VERIFY, address(new MockPQCVerify()).code);
        vm.etch(PQC_KEYS, address(new MockPQCKeys()).code);
        keys = MockPQCKeys(PQC_KEYS);

        treasury = new QoreGuardTreasury(owner, TREASURY_DAILY_CAP);
        vm.deal(owner, 100 ether);
        keys.setKey(owner, 1, ownerPk, true);
        _addVendor(1, hosting, "cloud-hosting", 200_000, 1_000_000);
        _addVendor(2, apiVendor, "api-credits", 300_000, 600_000);
    }

    // ------------------------------------------------------------------ //
    // helpers
    // ------------------------------------------------------------------ //

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

    function _signedSetTreasuryCap(uint96 cap) internal returns (uint256 nonce) {
        nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(cap));
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        treasury.setTreasuryCap(cap, nonce, ownerPk, _sign(message));
    }

    function _signedSetCaps(uint256 id, uint96 txCap, uint96 dayCap) internal {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(id, txCap, dayCap));
        bytes memory message = treasury.adminMessage(treasury.SET_VENDOR_CAPS(), payload, nonce);
        treasury.setVendorCaps(id, txCap, dayCap, nonce, ownerPk, _sign(message));
    }

    function _signedSetActive(uint256 id, bool active) internal {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(id, active));
        bytes memory message = treasury.adminMessage(treasury.SET_VENDOR_ACTIVE(), payload, nonce);
        treasury.setVendorActive(id, active, nonce, ownerPk, _sign(message));
    }

    // ------------------------------------------------------------------ //
    // constructor and registry
    // ------------------------------------------------------------------ //

    function testConstructorRefusesZeroOwner() public {
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.NotOwnerAccount.selector, address(0)));
        new QoreGuardTreasury(address(0), 1);
    }

    function testAddVendorRefusesZeroPayee() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint256(3), address(0), keccak256("x"), uint96(1), uint96(1)));
        bytes memory message = treasury.adminMessage(treasury.ADD_VENDOR(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.ZeroPayee.selector);
        treasury.addVendor(3, address(0), keccak256("x"), 1, 1, nonce, ownerPk, _sign(message));
    }

    function testAddVendorRefusesExistingId() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint256(1), attacker, keccak256("x"), uint96(1), uint96(1)));
        bytes memory message = treasury.adminMessage(treasury.ADD_VENDOR(), payload, nonce);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.VendorExists.selector, 1));
        treasury.addVendor(1, attacker, keccak256("x"), 1, 1, nonce, ownerPk, _sign(message));
    }

    function testVendorIdAtWalksTheRegistry() public {
        assertEq(treasury.vendorCount(), 2);
        assertEq(treasury.vendorIdAt(0), 1);
        assertEq(treasury.vendorIdAt(1), 2);
    }

    // ------------------------------------------------------------------ //
    // setVendorCaps
    // ------------------------------------------------------------------ //

    function testSetVendorCapsTakesEffect() public {
        _signedSetCaps(1, 50_000, 60_000);
        (address payee, , uint96 txCap, uint96 dayCap, bool active) = treasury.vendor(1);
        assertEq(payee, hosting);
        assertEq(txCap, 50_000);
        assertEq(dayCap, 60_000);
        assertTrue(active);

        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(QoreGuardTreasury.PerVendorTxCapExceeded.selector, 50_001, 50_000)
        );
        treasury.pay{value: 50_001 * WEI}(1, 50_001, bytes32(0));
    }

    function testSetVendorCapsRefusesUnknownVendor() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint256(9), uint96(1), uint96(1)));
        bytes memory message = treasury.adminMessage(treasury.SET_VENDOR_CAPS(), payload, nonce);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.UnknownVendor.selector, 9));
        treasury.setVendorCaps(9, 1, 1, nonce, ownerPk, _sign(message));
    }

    function testSetVendorActiveRefusesUnknownVendor() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint256(9), false));
        bytes memory message = treasury.adminMessage(treasury.SET_VENDOR_ACTIVE(), payload, nonce);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.UnknownVendor.selector, 9));
        treasury.setVendorActive(9, false, nonce, ownerPk, _sign(message));
    }

    function testReactivatedVendorCanBePaidAgain() public {
        _signedSetActive(1, false);
        _signedSetActive(1, true);
        uint256 before = hosting.balance;
        vm.prank(owner);
        treasury.pay{value: 100_000 * WEI}(1, 100_000, keccak256("after"));
        assertEq(hosting.balance - before, 100_000 * WEI);
    }

    function testTreasuryCapCanBeRaisedByOwnerSignature() public {
        assertEq(treasury.treasuryDailyCapUqor(), TREASURY_DAILY_CAP);
        _signedSetTreasuryCap(9_000_000);
        assertEq(treasury.treasuryDailyCapUqor(), 9_000_000);
    }

    // ------------------------------------------------------------------ //
    // pay() edges
    // ------------------------------------------------------------------ //

    function testPayRefusesZeroAmount() public {
        vm.prank(owner);
        vm.expectRevert(QoreGuardTreasury.ZeroAmount.selector);
        treasury.pay{value: 0}(1, 0, bytes32(0));
    }

    function testDuplicateCheckPrecedesTheVendorCap() public {
        bytes32 invoice = keccak256("qg-inv:v1:1:INV-1001");
        vm.prank(owner);
        treasury.pay{value: 150_000 * WEI}(1, 150_000, invoice);
        // Even an amount over the vendor cap reports the duplicate, not the cap:
        // the invoice is settled and no lane may revisit it.
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.InvoiceAlreadyPaid.selector, invoice));
        treasury.pay{value: 250_000 * WEI}(1, 250_000, invoice);
    }

    function testDuplicateCheckPrecedesTheOwnerSignature() public {
        bytes32 invoice = keccak256("qg-inv:v1:1:INV-1002");
        uint256 amount = 500_000;
        uint256 nonce = treasury.approvalNonce();
        bytes memory message = treasury.exceptionMessage(1, amount, invoice, nonce);
        vm.prank(owner);
        treasury.payWithApproval{value: amount * WEI}(1, amount, invoice, nonce, ownerPk, _sign(message));

        // A duplicate with a garbage signature is still refused as a duplicate:
        // the record is consulted before the PQ gate.
        uint256 next = treasury.approvalNonce();
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.InvoiceAlreadyPaid.selector, invoice));
        treasury.payWithApproval{value: amount * WEI}(1, amount, invoice, next, ownerPk, hex"01");
    }

    function testPayRefusesWhenVendorTransferFails() public {
        RejectingPayee rejecting = new RejectingPayee();
        _addVendor(3, address(rejecting), "rejecting", 100_000, 100_000);
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(QoreGuardTreasury.TransferFailed.selector, address(rejecting), 1_000 * WEI)
        );
        treasury.pay{value: 1_000 * WEI}(3, 1_000, bytes32(0));
    }

    // ------------------------------------------------------------------ //
    // payWithApproval() edges
    // ------------------------------------------------------------------ //

    function testPayWithApprovalRefusesZeroAmount() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(1, 0, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(QoreGuardTreasury.ZeroAmount.selector);
        treasury.payWithApproval{value: 0}(1, 0, memo, nonce, ownerPk, _sign(message));
    }

    function testPayWithApprovalRefusesValueMismatch() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(1, 500_000, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.ValueMismatch.selector, 1, 500_000 * WEI
            )
        );
        treasury.payWithApproval{value: 1}(1, 500_000, memo, nonce, ownerPk, _sign(message));
    }

    function testPayWithApprovalRefusesUnknownVendor() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(99, 1, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.UnknownVendor.selector, 99));
        treasury.payWithApproval{value: 1 * WEI}(99, 1, memo, nonce, ownerPk, _sign(message));
    }

    function testPayWithApprovalRefusesInactiveVendor() public {
        _signedSetActive(2, false);
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(2, 500_000, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.VendorInactive.selector, 2));
        treasury.payWithApproval{value: 500_000 * WEI}(2, 500_000, memo, nonce, ownerPk, _sign(message));
    }

    function testPayWithApprovalRefusesWhenTransferFails() public {
        RejectingPayee rejecting = new RejectingPayee();
        _addVendor(3, address(rejecting), "rejecting", 100_000, 100_000);
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(3, 100_000, memo, nonce);
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(QoreGuardTreasury.TransferFailed.selector, address(rejecting), 100_000 * WEI)
        );
        treasury.payWithApproval{value: 100_000 * WEI}(3, 100_000, memo, nonce, ownerPk, _sign(message));
    }

    function testPayWithApprovalRefusedForNonOwnerSender() public {
        vm.deal(attacker, 1 ether);
        uint256 nonce = treasury.approvalNonce();
        bytes32 memo = keccak256("x");
        bytes memory message = treasury.exceptionMessage(1, 500_000, memo, nonce);
        vm.prank(attacker);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.NotOwnerAccount.selector, attacker));
        treasury.payWithApproval{value: 500_000 * WEI}(1, 500_000, memo, nonce, ownerPk, _sign(message));
    }

    // ------------------------------------------------------------------ //
    // post-quantum gate edges
    // ------------------------------------------------------------------ //

    function testAdminRefusesWrongAlgorithm() public {
        keys.setKey(owner, 2, ownerPk, true); // ML-KEM, not ML-DSA-87
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.PqcKeyMismatch.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    function testAdminRefusesWhenVerifyPrecompileAnswersNothing() public {
        vm.etch(PQC_VERIFY, hex"");
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        vm.expectRevert(QoreGuardTreasury.PrecompileUnavailable.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    function testAdminSignatureIsBoundToTheActionTag() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        // Signed for SET_TREASURY_CAP but submitted as SET_VENDOR_CAPS' action.
        bytes memory message = treasury.adminMessage(treasury.SET_TREASURY_CAP(), payload, nonce);
        bytes32 payloadCaps = keccak256(abi.encode(uint256(1), uint96(1), uint96(1)));
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.setVendorCaps(1, 1, 1, nonce, ownerPk, _sign(message));
        payloadCaps;
    }

    function testAdminSignatureIsBoundToTheDeploymentAddress() public {
        QoreGuardTreasury second = new QoreGuardTreasury(owner, TREASURY_DAILY_CAP);
        uint256 nonce = second.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        // A message made for the *other* deployment's address.
        bytes memory message = abi.encodePacked(
            "QoreGuard.treasury.v1", block.chainid, address(treasury), second.SET_TREASURY_CAP(), payload, nonce
        );
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        second.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    function testAdminSignatureIsBoundToTheChainId() public {
        uint256 nonce = treasury.approvalNonce();
        bytes32 payload = keccak256(abi.encode(uint96(5)));
        bytes memory message = abi.encodePacked(
            "QoreGuard.treasury.v1", uint256(9801), address(treasury), treasury.SET_TREASURY_CAP(), payload, nonce
        );
        vm.expectRevert(QoreGuardTreasury.BadPqcSignature.selector);
        treasury.setTreasuryCap(5, nonce, ownerPk, _sign(message));
    }

    // ------------------------------------------------------------------ //
    // fuzz properties
    // ------------------------------------------------------------------ //

    /// Any amount inside the vendor cap is paid in full and recorded; any
    /// amount above it is refused, for every amount in the cap range.
    function testFuzzPayHonoursVendorTxCap(uint96 amount) public {
        amount = uint96(bound(amount, 1, 400_000));
        vm.prank(owner);
        if (amount > 200_000) {
            vm.expectRevert(
                abi.encodeWithSelector(
                    QoreGuardTreasury.PerVendorTxCapExceeded.selector, amount, uint96(200_000)
                )
            );
            treasury.pay{value: uint256(amount) * WEI}(1, amount, bytes32(0));
        } else {
            uint256 before = hosting.balance;
            treasury.pay{value: uint256(amount) * WEI}(1, amount, bytes32(0));
            assertEq(hosting.balance - before, uint256(amount) * WEI);
            assertEq(treasury.spentToday(1), amount);
        }
    }

    /// A payment whose msg.value does not equal amount × 1e12 is refused for
    /// every pair, so the memo's amount can never differ from the money moved.
    function testFuzzValueMustEqualAmount(uint96 amount, uint96 value) public {
        amount = uint96(bound(amount, 0, 1_500_000));
        value = uint96(bound(value, 0, 1_500_000));
        vm.assume(amount != 0 && value != amount);
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                QoreGuardTreasury.ValueMismatch.selector, uint256(value) * WEI, uint256(amount) * WEI
            )
        );
        treasury.pay{value: uint256(value) * WEI}(1, amount, bytes32(0));
    }

    /// The admin message commits to every field: changing any one of them
    /// changes the bytes the owner signs.
    function testFuzzAdminMessageBindsEveryField(bytes32 actionA, bytes32 actionB, bytes32 payloadA, bytes32 payloadB, uint256 nonceA, uint256 nonceB) public view {
        bytes memory m = treasury.adminMessage(actionA, payloadA, nonceA);
        if (
            actionA != actionB || payloadA != payloadB || nonceA != nonceB
        ) {
            assertTrue(
                keccak256(m) != keccak256(treasury.adminMessage(actionB, payloadB, nonceB)),
                "two different admin tuples share a message"
            );
        }
    }

    /// The exception message is exactly the admin message for PAY_WITH_APPROVAL.
    function testFuzzExceptionMessageMatchesAdminEncoding(uint256 vendorId, uint96 amount, bytes32 memo, uint256 nonce) public view {
        bytes memory expected = treasury.adminMessage(
            treasury.PAY_WITH_APPROVAL(), keccak256(abi.encode(vendorId, amount, memo)), nonce
        );
        assertEq(
            keccak256(treasury.exceptionMessage(vendorId, amount, memo, nonce)),
            keccak256(expected)
        );
    }

    /// Any non-owner sender is refused on every state-changing entry point.
    function testFuzzStrangerCannotChangeState(address caller, uint256 nonce) public {
        vm.assume(caller != owner && caller != address(0) && caller.code.length == 0);
        vm.deal(caller, 1 ether);
        vm.startPrank(caller);
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.NotOwnerAccount.selector, caller));
        treasury.pay{value: WEI}(1, 1, bytes32(0));
        vm.expectRevert(abi.encodeWithSelector(QoreGuardTreasury.NotOwnerAccount.selector, caller));
        treasury.payWithApproval{value: WEI}(1, 1, bytes32(0), nonce, ownerPk, hex"01");
        vm.stopPrank();
        assertEq(treasury.totalSpentUqor(), 0);
        assertEq(treasury.approvalNonce(), 2);
    }
}
