// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title QoreGuardTreasury
/// @notice On-chain policy gate for an AI agent that pays an owner's bills on
///         QoreChain. Two independent gates bound every payment:
///
///         1. the chain's account-level spending rule, enforced by
///            `x/abstractaccount` on the owner's account (per-tx limit, daily
///            limit, allowed denoms, and the list of EVM calls the agent's key
///            may make at all), and
///         2. this contract's business policy: vendor allowlist, per-vendor
///            per-tx and daily caps, a treasury-wide daily cap, and a full
///            audit trail.
///
///         Every policy change needs a fresh ML-DSA-87 (FIPS 204) signature
///         from the owner, verified on-chain by the `pqcVerify` precompile
///         (`0x…0a01`) against the key registered for the owner account
///         (`pqcKeyStatus`, `0x…0a02`). A stolen classical (ECDSA) owner key
///         therefore cannot add a payee or raise a cap.
///
///         Value flow is stateless: each payment arrives as `msg.value` and is
///         forwarded to the vendor in the same transaction, so the treasury
///         stays in the owner's account and the chain sees - and bounds - the
///         actual outflow on every call.
contract QoreGuardTreasury {
    // ------------------------------------------------------------------ //
    // QoreChain post-quantum precompiles (testnet qorechain-diana)
    // ------------------------------------------------------------------ //
    address internal constant PQC_VERIFY = 0x0000000000000000000000000000000000000a01;
    address internal constant PQC_KEYS = 0x0000000000000000000000000000000000000a02;

    /// @dev Domain tag of every message this contract asks the owner to sign.
    string public constant DOMAIN = "QoreGuard.treasury.v1";

    /// @dev 1 QOR = 1e18 wei on the EVM lane, 1e6 uqor on the native lane.
    uint256 public constant WEI_PER_UQOR = 1e12;

    /// @dev Action tags bound into every administrative signature.
    bytes32 public constant ADD_VENDOR = keccak256("addVendor");
    bytes32 public constant SET_VENDOR_ACTIVE = keccak256("setVendorActive");
    bytes32 public constant SET_VENDOR_CAPS = keccak256("setVendorCaps");
    bytes32 public constant SET_TREASURY_CAP = keccak256("setTreasuryCap");
    bytes32 public constant PAY_WITH_APPROVAL = keccak256("payWithApproval");

    /// @notice The 0x address of the treasury owner (the PQC-required account).
    address public immutable ownerAccount;

    struct Vendor {
        address payee;
        bytes32 labelHash;
        uint96 perTxCapUqor;
        uint96 dailyCapUqor;
        bool active;
    }

    mapping(uint256 => Vendor) private _vendors;
    uint256[] private _vendorIds;

    /// @notice Treasury-wide cap on everything the agent may pay in one UTC day.
    uint96 public treasuryDailyCapUqor;

    mapping(uint256 => uint96) public spentOnDayUqor;
    uint256 public totalSpentUqor;

    /// @notice Monotonic counter bound into every owner signature (replay guard).
    uint256 public approvalNonce;

    // ------------------------------------------------------------------ //
    // Events: the audit trail a dashboard or an auditor reads back.
    // ------------------------------------------------------------------ //
    event VendorPaid(
        uint256 indexed vendorId,
        address indexed payee,
        uint256 amountUqor,
        bytes32 memoHash,
        uint256 dayIndex,
        uint256 daySpentUqor
    );
    event VendorPaidWithApproval(
        uint256 indexed vendorId,
        address indexed payee,
        uint256 amountUqor,
        bytes32 memoHash,
        uint256 approvalNonce,
        uint256 dayIndex
    );
    event PolicyUpdated(bytes32 indexed action, bytes32 payloadHash, uint256 approvalNonce);
    event VendorChanged(uint256 indexed vendorId, address payee, uint96 perTxCapUqor, uint96 dailyCapUqor, bool active);

    // ------------------------------------------------------------------ //
    // Errors
    // ------------------------------------------------------------------ //
    error NotOwnerAccount(address caller);
    error UnknownVendor(uint256 vendorId);
    error VendorExists(uint256 vendorId);
    error VendorInactive(uint256 vendorId);
    error PerVendorTxCapExceeded(uint256 amountUqor, uint96 capUqor);
    error PerVendorDayCapExceeded(uint256 wouldBeUqor, uint96 capUqor);
    error TreasuryDayCapExceeded(uint256 wouldBeUqor, uint96 capUqor);
    error ValueMismatch(uint256 sentWei, uint256 expectedWei);
    error PqcKeyNotRegistered();
    error PqcKeyNotProven();
    error PqcKeyMismatch();
    error BadPqcSignature();
    error StaleApprovalNonce(uint256 given, uint256 expected);
    error PrecompileUnavailable();
    error TransferFailed(address payee, uint256 amountWei);
    error ZeroAmount();
    error ZeroPayee();

    constructor(address ownerAccount_, uint96 treasuryDailyCapUqor_) {
        if (ownerAccount_ == address(0)) revert NotOwnerAccount(address(0));
        ownerAccount = ownerAccount_;
        treasuryDailyCapUqor = treasuryDailyCapUqor_;
    }

    modifier onlyOwnerAccount() {
        if (msg.sender != ownerAccount) revert NotOwnerAccount(msg.sender);
        _;
    }

    // ------------------------------------------------------------------ //
    // Agent lane: the only call the agent's linked key may make
    // ------------------------------------------------------------------ //

    /// @notice Pay a registered vendor inside every cap.
    /// @param vendorId id assigned by the owner in a PQ-signed `addVendor`
    /// @param amountUqor amount in uqor (1 QOR = 1e6); must equal msg.value/1e12
    /// @param memoHash keccak256 of the human-readable memo, for the audit trail
    function pay(uint256 vendorId, uint256 amountUqor, bytes32 memoHash)
        external
        payable
        onlyOwnerAccount
    {
        if (amountUqor == 0) revert ZeroAmount();
        if (msg.value != amountUqor * WEI_PER_UQOR) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);

        Vendor storage v = _vendors[vendorId];
        if (v.payee == address(0)) revert UnknownVendor(vendorId);
        if (!v.active) revert VendorInactive(vendorId);
        if (amountUqor > v.perTxCapUqor) revert PerVendorTxCapExceeded(amountUqor, v.perTxCapUqor);

        uint256 day = block.timestamp / 1 days;
        uint256 vendorDayAfter = uint256(spentOnDayUqor[_vendorDayKey(vendorId, day)]) + amountUqor;
        if (vendorDayAfter > v.dailyCapUqor) revert PerVendorDayCapExceeded(vendorDayAfter, v.dailyCapUqor);

        uint256 treasuryDayAfter = uint256(spentOnDayUqor[day]) + amountUqor;
        if (treasuryDayAfter > treasuryDailyCapUqor) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);

        spentOnDayUqor[_vendorDayKey(vendorId, day)] = uint96(vendorDayAfter);
        spentOnDayUqor[day] = uint96(treasuryDayAfter);
        totalSpentUqor += amountUqor;

        emit VendorPaid(vendorId, v.payee, amountUqor, memoHash, day, treasuryDayAfter);

        (bool ok,) = payable(v.payee).call{value: msg.value}("");
        if (!ok) revert TransferFailed(v.payee, msg.value);
    }

    /// @notice Exception path: a payment above a vendor cap that the owner
    ///         approved with a fresh ML-DSA-87 signature over exactly these
    ///         arguments. The agent can submit it, but can never create it.
    function payWithApproval(
        uint256 vendorId,
        uint256 amountUqor,
        bytes32 memoHash,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) external payable onlyOwnerAccount {
        if (amountUqor == 0) revert ZeroAmount();
        if (msg.value != amountUqor * WEI_PER_UQOR) revert ValueMismatch(msg.value, amountUqor * WEI_PER_UQOR);

        Vendor storage v = _vendors[vendorId];
        if (v.payee == address(0)) revert UnknownVendor(vendorId);
        if (!v.active) revert VendorInactive(vendorId);

        bytes32 payloadHash = keccak256(abi.encode(vendorId, amountUqor, memoHash));
        _requireOwnerSignature(PAY_WITH_APPROVAL, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);

        uint256 day = block.timestamp / 1 days;
        // The treasury-wide cap still holds: an exception is a one-off payment,
        // not a way to lift the budget the owner set for the whole day.
        uint256 treasuryDayAfter = uint256(spentOnDayUqor[day]) + amountUqor;
        if (treasuryDayAfter > treasuryDailyCapUqor) revert TreasuryDayCapExceeded(treasuryDayAfter, treasuryDailyCapUqor);
        spentOnDayUqor[day] = uint96(treasuryDayAfter);
        totalSpentUqor += amountUqor;

        emit VendorPaidWithApproval(vendorId, v.payee, amountUqor, memoHash, approvalNonce_, day);

        (bool ok,) = payable(v.payee).call{value: msg.value}("");
        if (!ok) revert TransferFailed(v.payee, msg.value);
    }

    // ------------------------------------------------------------------ //
    // Owner lane: every change is post-quantum signed
    // ------------------------------------------------------------------ //

    function addVendor(
        uint256 vendorId,
        address payee,
        bytes32 labelHash,
        uint96 perTxCapUqor,
        uint96 dailyCapUqor,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) external {
        if (payee == address(0)) revert ZeroPayee();
        if (_vendors[vendorId].payee != address(0)) revert VendorExists(vendorId);
        bytes32 payloadHash = keccak256(abi.encode(vendorId, payee, labelHash, perTxCapUqor, dailyCapUqor));
        _requireOwnerSignature(ADD_VENDOR, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);

        _vendors[vendorId] = Vendor({
            payee: payee,
            labelHash: labelHash,
            perTxCapUqor: perTxCapUqor,
            dailyCapUqor: dailyCapUqor,
            active: true
        });
        _vendorIds.push(vendorId);
        emit VendorChanged(vendorId, payee, perTxCapUqor, dailyCapUqor, true);
    }

    function setVendorActive(
        uint256 vendorId,
        bool active,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) external {
        Vendor storage v = _vendors[vendorId];
        if (v.payee == address(0)) revert UnknownVendor(vendorId);
        bytes32 payloadHash = keccak256(abi.encode(vendorId, active));
        _requireOwnerSignature(SET_VENDOR_ACTIVE, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);
        v.active = active;
        emit VendorChanged(vendorId, v.payee, v.perTxCapUqor, v.dailyCapUqor, active);
    }

    function setVendorCaps(
        uint256 vendorId,
        uint96 perTxCapUqor,
        uint96 dailyCapUqor,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) external {
        Vendor storage v = _vendors[vendorId];
        if (v.payee == address(0)) revert UnknownVendor(vendorId);
        bytes32 payloadHash = keccak256(abi.encode(vendorId, perTxCapUqor, dailyCapUqor));
        _requireOwnerSignature(SET_VENDOR_CAPS, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);
        v.perTxCapUqor = perTxCapUqor;
        v.dailyCapUqor = dailyCapUqor;
        emit VendorChanged(vendorId, v.payee, perTxCapUqor, dailyCapUqor, v.active);
    }

    function setTreasuryCap(
        uint96 treasuryDailyCapUqor_,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) external {
        bytes32 payloadHash = keccak256(abi.encode(treasuryDailyCapUqor_));
        _requireOwnerSignature(SET_TREASURY_CAP, payloadHash, approvalNonce_, pqcPubkey, pqcSignature);
        treasuryDailyCapUqor = treasuryDailyCapUqor_;
    }

    // ------------------------------------------------------------------ //
    // Views
    // ------------------------------------------------------------------ //

    function vendor(uint256 vendorId)
        external
        view
        returns (address payee, bytes32 labelHash, uint96 perTxCapUqor, uint96 dailyCapUqor, bool active)
    {
        Vendor storage v = _vendors[vendorId];
        return (v.payee, v.labelHash, v.perTxCapUqor, v.dailyCapUqor, v.active);
    }

    function vendorCount() external view returns (uint256) {
        return _vendorIds.length;
    }

    function vendorIdAt(uint256 index) external view returns (uint256) {
        return _vendorIds[index];
    }

    function spentToday(uint256 vendorId) external view returns (uint256) {
        return spentOnDayUqor[_vendorDayKey(vendorId, block.timestamp / 1 days)];
    }

    function treasurySpentToday() external view returns (uint256) {
        return spentOnDayUqor[block.timestamp / 1 days];
    }

    /// @notice The exact message an owner signs for an administrative action.
    ///         Everything binds the chain, this contract and the nonce, so a
    ///         signature cannot be replayed here or on another deployment.
    function adminMessage(bytes32 action, bytes32 payloadHash, uint256 approvalNonce_)
        public
        view
        returns (bytes memory)
    {
        return abi.encodePacked(DOMAIN, block.chainid, address(this), action, payloadHash, approvalNonce_);
    }

    /// @notice The exact message an owner signs to approve one over-cap payment.
    function exceptionMessage(uint256 vendorId, uint256 amountUqor, bytes32 memoHash, uint256 approvalNonce_)
        public
        view
        returns (bytes memory)
    {
        return adminMessage(PAY_WITH_APPROVAL, keccak256(abi.encode(vendorId, amountUqor, memoHash)), approvalNonce_);
    }

    // ------------------------------------------------------------------ //
    // Internals
    // ------------------------------------------------------------------ //

    function _requireOwnerSignature(
        bytes32 action,
        bytes32 payloadHash,
        uint256 approvalNonce_,
        bytes calldata pqcPubkey,
        bytes calldata pqcSignature
    ) internal {
        if (approvalNonce_ != approvalNonce) revert StaleApprovalNonce(approvalNonce_, approvalNonce);

        (bool registered, uint8 algorithmId, bytes memory registeredKey, bool proven) =
            _pqcKeyStatus(ownerAccount);
        if (!registered || registeredKey.length == 0) revert PqcKeyNotRegistered();
        if (!proven) revert PqcKeyNotProven();
        if (algorithmId != 1) revert PqcKeyMismatch(); // 1 = ML-DSA-87
        if (keccak256(registeredKey) != keccak256(pqcPubkey)) revert PqcKeyMismatch();

        if (!_pqcVerify(pqcPubkey, pqcSignature, adminMessage(action, payloadHash, approvalNonce_))) {
            revert BadPqcSignature();
        }

        approvalNonce = approvalNonce_ + 1;
        emit PolicyUpdated(action, payloadHash, approvalNonce_);
    }

    /// @dev Calls `pqcKeyStatus(address)` and refuses empty return data: an
    ///      inactive precompile answers success with no data, which must never
    ///      read as "registered".
    function _pqcKeyStatus(address account)
        internal
        view
        returns (bool registered, uint8 algorithmId, bytes memory pubkey, bool proven)
    {
        (bool ok, bytes memory ret) =
            PQC_KEYS.staticcall(abi.encodeWithSignature("pqcKeyStatus(address)", account));
        if (!ok || ret.length < 128) revert PrecompileUnavailable();
        return abi.decode(ret, (bool, uint8, bytes, bool));
    }

    function _pqcVerify(bytes memory pubkey, bytes memory signature, bytes memory message)
        internal
        view
        returns (bool)
    {
        (bool ok, bytes memory ret) =
            PQC_VERIFY.staticcall(abi.encodeWithSignature("pqcVerify(bytes,bytes,bytes)", pubkey, signature, message));
        if (!ok || ret.length < 32) revert PrecompileUnavailable();
        return abi.decode(ret, (bool));
    }

    /// @dev Vendor day counters live in their own key space so they never
    ///      collide with the treasury-wide counter at `day`.
    function _vendorDayKey(uint256 vendorId, uint256 day) internal pure returns (uint256) {
        return uint256(keccak256(abi.encode(vendorId, day)));
    }

    receive() external payable {
        revert ValueMismatch(msg.value, 0);
    }
}
