// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import { VelaRewardVault } from "./VelaRewardVault.sol";

/**
 * @title VelaRewardVaultFactory — CANDIDATE (Stage 2). NOT compiled here, NOT audited, NOT deployed.
 * @notice Creates NEW isolated reward-enabled vaults. It never attaches to, upgrades, or reads existing
 *         Vela vaults. Each vault holds its own principal and its own separately funded reward reserve;
 *         the factory never holds funds itself.
 *
 *  - Only the factory owner may create vaults (owner authorization).
 *  - Every vault is bound to the factory's expected chain id (DogeOS testnet: 6281971).
 *  - The vault owner is set explicitly; the factory is never the vault owner.
 */
contract VelaRewardVaultFactory {
    uint256 private constant BPS = 10_000;

    address public immutable owner;
    uint256 public immutable expectedChainId;

    address[] private _vaults;
    mapping(address => bool) public isVault;

    event VaultCreated(address indexed vault, address indexed vaultOwner, uint256 aprBps, uint256 index);

    error OnlyOwner();
    error WrongChain();
    error ZeroAddress();
    error RateOutOfRange();
    error DirectTransferRejected();

    modifier onlyOwner() { if (msg.sender != owner) revert OnlyOwner(); _; }
    modifier onChain() { if (block.chainid != expectedChainId) revert WrongChain(); _; }

    constructor(uint256 _expectedChainId) {
        if (block.chainid != _expectedChainId) revert WrongChain();
        owner = msg.sender;
        expectedChainId = _expectedChainId;
    }

    /// @param vaultOwner account authorized to top up the reserve, change rate and seize
    /// @param aprBps simple APR in basis points (500 = 5%)
    function createVault(address vaultOwner, uint256 aprBps) external onlyOwner onChain returns (address vault) {
        if (vaultOwner == address(0)) revert ZeroAddress();
        if (aprBps > BPS) revert RateOutOfRange();
        vault = address(new VelaRewardVault(vaultOwner, aprBps, expectedChainId));
        isVault[vault] = true;
        _vaults.push(vault);
        emit VaultCreated(vault, vaultOwner, aprBps, _vaults.length - 1);
    }

    function vaultCount() external view returns (uint256) { return _vaults.length; }

    function vaultAt(uint256 index) external view returns (address) { return _vaults[index]; }

    receive() external payable { revert DirectTransferRejected(); }
    fallback() external payable { revert DirectTransferRejected(); }
}
