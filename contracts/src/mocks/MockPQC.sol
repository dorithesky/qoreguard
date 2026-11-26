// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Test-only stand-ins for the two QoreChain post-quantum precompiles.
///         `forge test` etches these at 0x…0a01 and 0x…0a02 so the policy
///         logic can be exercised without a live chain. On the testnet the
///         node answers those two addresses itself.
///
///         A "signature" here is `0x01 || message`, so the mock still binds the
///         signature to the exact message the contract hands it: a signature
///         made for one payment cannot authorise another.
contract MockPQCVerify {
    function pqcVerify(bytes calldata, bytes calldata signature, bytes calldata message)
        external
        pure
        returns (bool)
    {
        if (signature.length < 2 || signature[0] != 0x01) return false;
        return keccak256(signature[1:]) == keccak256(message);
    }
}

contract MockPQCKeys {
    struct Key {
        bool registered;
        uint8 algorithmId;
        bytes pubkey;
        bool proven;
    }

    mapping(address => Key) internal _keys;

    function setKey(address account, uint8 algorithmId, bytes calldata pubkey, bool proven) external {
        _keys[account] = Key({registered: true, algorithmId: algorithmId, pubkey: pubkey, proven: proven});
    }

    function clearKey(address account) external {
        delete _keys[account];
    }

    function pqcKeyStatus(address account)
        external
        view
        returns (bool registered, uint8 algorithmId, bytes memory pubkey, bool possessionProven)
    {
        Key storage k = _keys[account];
        return (k.registered, k.algorithmId, k.pubkey, k.proven);
    }
}
