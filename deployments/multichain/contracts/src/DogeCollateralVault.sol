// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {AgentPermissions} from "./AgentPermissions.sol";
import {VaultEvents} from "./VaultEvents.sol";

/// Testnet collateral is real native test DOGE. Stablecoin loans are OFF-CHAIN
/// DEMO accounting: no stablecoin is minted, lent or redeemed by this contract.
/// A dedicated demo attestor certifies ledger transitions. Only the owner can
/// submit settlement or withdraw; agents can stop but cannot move collateral.
contract DogeCollateralVault is Ownable, Pausable, ReentrancyGuard, VaultEvents {
    enum LoanState { Idle, Requested, Active, Settled, Defaulted }
    address public immutable settlementSigner;
    address payable public immutable liquidationTreasury;
    uint256 public collateral;
    bytes32 public loanId;
    uint256 public loanNonce;
    uint256 public principalMicro;
    LoanState public loanState;
    mapping(bytes32 => bool) public usedLoanIds;
    address public agent;
    AgentPermissions.Limits public limits;
    uint256 public permissionRevision;
    bool public trading;
    uint256 public badDebtMicro;
    error InvalidState();
    error InvalidCertificate();
    error CollateralLocked();
    error TransferFailed();
    error ImmutableOwnership();

    constructor(address initialOwner, address signer, address payable treasury) Ownable(initialOwner) {
        if (signer == address(0) || treasury == address(0)) revert InvalidState();
        settlementSigner = signer;
        liquidationTreasury = treasury;
    }
    function transferOwnership(address) public pure override { revert ImmutableOwnership(); }
    function renounceOwnership() public pure override { revert ImmutableOwnership(); }
    receive() external payable onlyOwner whenNotPaused nonReentrant { _deposit(); }
    function deposit() external payable onlyOwner whenNotPaused nonReentrant { _deposit(); }
    function _deposit() private {
        if (msg.value == 0 || loanState == LoanState.Defaulted) revert InvalidState();
        collateral += msg.value;
        emit CollateralDeposited(owner(), msg.value, collateral);
    }
    function withdrawable() public view returns (uint256) {
        return !paused() && !trading && (loanState == LoanState.Idle || loanState == LoanState.Settled) ? collateral : 0;
    }
    function withdraw(uint256 amount) external onlyOwner whenNotPaused nonReentrant {
        if (amount == 0 || amount > withdrawable()) revert CollateralLocked();
        collateral -= amount;
        (bool ok,) = payable(owner()).call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit CollateralWithdrawn(owner(), amount, collateral);
    }
    function requestDemoBorrow(bytes32 id, uint256 amountMicro) external onlyOwner whenNotPaused {
        if (trading || collateral == 0 || id == bytes32(0) || usedLoanIds[id] || amountMicro == 0 ||
            amountMicro > 1_000_000e6 || (loanState != LoanState.Idle && loanState != LoanState.Settled)) revert InvalidState();
        _revoke();
        usedLoanIds[id] = true;
        loanId = id; loanNonce++; principalMicro = amountMicro;
        loanState = LoanState.Requested;
        emit BorrowRequested(id, amountMicro, loanNonce);
    }
    function certificateHash(uint8 action, uint256 a, uint256 b, uint256 c, uint64 deadline) public view returns(bytes32) {
        return keccak256(abi.encode(keccak256("ADP_DEMO_V1"), block.chainid, address(this), owner(),
            loanNonce, action, loanId, a, b, c, deadline));
    }
    function _verify(uint8 action, uint256 a, uint256 b, uint256 c, uint64 deadline, bytes calldata signature) private view {
        if (deadline < block.timestamp || deadline > block.timestamp + 1 hours ||
            ECDSA.recover(MessageHashUtils.toEthSignedMessageHash(certificateHash(action,a,b,c,deadline)),signature) != settlementSigner)
            revert InvalidCertificate();
    }
    /// Acknowledges an already-issued simulated database credit; transfers no USDC.
    function acknowledgeDemoLoan(uint64 deadline, bytes calldata signature) external onlyOwner whenNotPaused {
        if (loanState != LoanState.Requested) revert InvalidState();
        _verify(1, principalMicro, 0, 0, deadline, signature);
        loanState = LoanState.Active;
        emit LoanIssued(loanId, principalMicro, true);
    }
    /// An uncredited request may be cancelled only with the ledger attestor's proof.
    function cancelUnissuedLoan(uint64 deadline, bytes calldata signature) external onlyOwner {
        if (loanState != LoanState.Requested) revert InvalidState();
        _verify(3, 0, 0, 0, deadline, signature);
        loanState = LoanState.Settled;
    }
    function authorizeAgent(address next, AgentPermissions.Limits calldata p) external onlyOwner whenNotPaused {
        if (next == address(0) || loanState != LoanState.Active) revert InvalidState();
        AgentPermissions.validate(p);
        if (trading) _stop();
        agent = next; limits = p; permissionRevision++;
        emit AgentAuthorized(next,permissionRevision,p.expiresAt);
    }
    function revokeAgent() external onlyOwner { _revoke(); }
    function _revoke() private {
        if (trading) _stop();
        address old = agent; agent = address(0); delete limits; permissionRevision++;
        if (old != address(0)) emit AgentRevoked(old,permissionRevision);
    }
    function startTrading() external onlyOwner whenNotPaused {
        if (loanState != LoanState.Active || agent == address(0) || limits.expiresAt <= block.timestamp || trading) revert InvalidState();
        trading = true; emit TradingStarted(loanId,permissionRevision);
    }
    function stopTrading() external {
        if (msg.sender != owner() && msg.sender != agent) revert OwnableUnauthorizedAccount(msg.sender);
        _stop();
    }
    function _stop() private {
        if (trading) { trading = false; emit TradingStopped(loanId); }
    }
    function emergencyPause() external onlyOwner { _revoke(); _pause(); }
    function unpause() external onlyOwner { _unpause(); }
    /// Off-chain paper limits are enforced by the backend governor, NOT this view.
    function agentAuthorized() external view returns(bool) {
        return !paused() && trading && agent != address(0) && limits.expiresAt > block.timestamp;
    }
    /// Owner explicitly accepts any TEST-DOGE loss. Simulated lending never
    /// creates real stablecoin obligations. A default is retained, never erased.
    function settleDemoLoan(uint256 repaidMicro, uint256 seizeWei, uint256 remainingBadDebtMicro,
                            uint64 deadline, bytes calldata signature) external onlyOwner nonReentrant {
        if (loanState != LoanState.Active || repaidMicro + remainingBadDebtMicro < principalMicro ||
            seizeWei > collateral || (remainingBadDebtMicro != 0 && seizeWei != collateral)) revert InvalidState();
        _verify(2,repaidMicro,seizeWei,remainingBadDebtMicro,deadline,signature);
        _revoke();
        badDebtMicro = remainingBadDebtMicro;
        loanState = remainingBadDebtMicro == 0 ? LoanState.Settled : LoanState.Defaulted;
        collateral -= seizeWei;
        if (seizeWei != 0) {
            (bool ok,) = liquidationTreasury.call{value:seizeWei}("");
            if (!ok) revert TransferFailed();
            emit LiquidationTriggered(loanId,seizeWei,remainingBadDebtMicro,true);
        }
        emit LoanRepaid(loanId,repaidMicro,remainingBadDebtMicro,true);
    }
}
