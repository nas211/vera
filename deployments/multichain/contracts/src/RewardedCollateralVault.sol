// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {AgentPermissions} from "./AgentPermissions.sol";
import {NativeRewardPool} from "./NativeRewardPool.sol";
import {VaultEvents} from "./VaultEvents.sol";

/// New candidate only: native test DOGE collateral with rewards from the shared project pool.
/// Existing deployed DogeCollateralVault contracts are not upgraded or migrated.
/// Testnet collateral is real native test DOGE. Stablecoin loans are OFF-CHAIN
/// DEMO accounting: no stablecoin is minted, lent or redeemed by this contract.
/// A dedicated demo attestor certifies ledger transitions. Only the owner can
/// submit settlement or withdraw; agents can stop but cannot move collateral.
contract RewardedCollateralVault is Ownable, Pausable, ReentrancyGuard, VaultEvents {
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

    uint256 public immutable expectedChainId;
    address public immutable rewardManager;
    NativeRewardPool public immutable rewardPool;
    uint32 public stopBudgetMicro = 2_000_000;
    event RewardReserveFunded(address indexed funder, uint256 amount);
    event RewardClaimed(address indexed owner, uint256 amount);
    error InvalidRewardTerms();
    error RewardTransferRejected();
    error WrongChain();
    modifier onChain() { if (block.chainid != expectedChainId) revert WrongChain(); _; }

    constructor(address initialOwner, address signer, address payable treasury, NativeRewardPool pool) Ownable(initialOwner) {
        if ((block.chainid != 6281971 && block.chainid != 31337) || address(pool)==address(0) || pool.factory()!=msg.sender || pool.expectedChainId()!=block.chainid) revert InvalidRewardTerms();
        expectedChainId = block.chainid;
        rewardManager = pool.rewardManager();
        rewardPool = pool;
        if (signer == address(0) || treasury == address(0)) revert InvalidState();
        settlementSigner = signer;
        liquidationTreasury = treasury;
    }
    function transferOwnership(address) public pure override { revert ImmutableOwnership(); }
    function renounceOwnership() public pure override { revert ImmutableOwnership(); }
    receive() external payable { revert RewardTransferRejected(); }
    function deposit() external payable onChain onlyOwner whenNotPaused nonReentrant { _deposit(); }
    function _deposit() private {
        if (msg.value == 0 || loanState == LoanState.Defaulted) revert InvalidState();
        collateral += msg.value;
        rewardPool.updatePrincipal(collateral);
        emit CollateralDeposited(owner(), msg.value, collateral);
    }
    function withdrawable() public view returns (uint256) {
        return !paused() && !trading && (loanState == LoanState.Idle || loanState == LoanState.Settled) ? collateral : 0;
    }
    function withdraw(uint256 amount) external onChain onlyOwner whenNotPaused nonReentrant {
        if (amount == 0 || amount > withdrawable()) revert CollateralLocked();
        collateral -= amount;
        rewardPool.updatePrincipal(collateral);
        (bool ok,) = payable(owner()).call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit CollateralWithdrawn(owner(), amount, collateral);
    }
    function requestDemoBorrow(bytes32 id, uint256 amountMicro) external nonReentrant onChain onlyOwner whenNotPaused {
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
    function acknowledgeDemoLoan(uint64 deadline, bytes calldata signature) external nonReentrant onChain onlyOwner whenNotPaused {
        if (loanState != LoanState.Requested) revert InvalidState();
        _verify(1, principalMicro, 0, 0, deadline, signature);
        loanState = LoanState.Active;
        emit LoanIssued(loanId, principalMicro, true);
    }
    /// An uncredited request may be cancelled only with the ledger attestor's proof.
    function cancelUnissuedLoan(uint64 deadline, bytes calldata signature) external nonReentrant onChain onlyOwner {
        if (loanState != LoanState.Requested) revert InvalidState();
        _verify(3, 0, 0, 0, deadline, signature);
        loanState = LoanState.Settled;
    }
    function authorizeAgent(address next, AgentPermissions.Limits calldata p) external nonReentrant onChain onlyOwner whenNotPaused {
        if (next == address(0) || loanState != LoanState.Active) revert InvalidState();
        AgentPermissions.validate(p);
        if (trading) _stop();
        agent = next; limits = p;
        if (p.maxLoss < stopBudgetMicro) stopBudgetMicro = p.maxLoss;
        limits.maxLoss = stopBudgetMicro;
        permissionRevision++;
        emit AgentAuthorized(next,permissionRevision,p.expiresAt);
    }
    function revokeAgent() external nonReentrant onChain onlyOwner { _revoke(); }
    function _revoke() private {
        if (trading) _stop();
        address old = agent; agent = address(0); delete limits; permissionRevision++;
        if (old != address(0)) emit AgentRevoked(old,permissionRevision);
    }
    function startTrading() external nonReentrant onChain onlyOwner whenNotPaused {
        if (loanState != LoanState.Active || agent == address(0) || limits.expiresAt <= block.timestamp || trading) revert InvalidState();
        trading = true; emit TradingStarted(loanId,permissionRevision);
    }
    function stopTrading() external nonReentrant onChain {
        if (msg.sender != owner() && msg.sender != agent) revert OwnableUnauthorizedAccount(msg.sender);
        _stop();
    }
    function _stop() private {
        if (trading) { trading = false; emit TradingStopped(loanId); }
    }
    function emergencyPause() external nonReentrant onChain onlyOwner { _revoke(); _pause(); }
    function unpause() external nonReentrant onChain onlyOwner { _unpause(); }
    /// Off-chain paper limits are enforced by the backend governor, NOT this view.
    function agentAuthorized() external view returns(bool) {
        return !paused() && trading && agent != address(0) && limits.expiresAt > block.timestamp;
    }
    /// Owner explicitly accepts any TEST-DOGE loss. Simulated lending never
    /// creates real stablecoin obligations. A default is retained, never erased.
    function settleDemoLoan(uint256 repaidMicro, uint256 seizeWei, uint256 remainingBadDebtMicro,
                            uint64 deadline, bytes calldata signature) external onChain onlyOwner nonReentrant {
        if (loanState != LoanState.Active || repaidMicro + remainingBadDebtMicro < principalMicro ||
            seizeWei > collateral || (remainingBadDebtMicro != 0 && seizeWei != collateral)) revert InvalidState();
        _verify(2,repaidMicro,seizeWei,remainingBadDebtMicro,deadline,signature);
        _revoke();
        badDebtMicro = remainingBadDebtMicro;
        loanState = remainingBadDebtMicro == 0 ? LoanState.Settled : LoanState.Defaulted;
        collateral -= seizeWei;
        rewardPool.updatePrincipal(collateral);
        if (seizeWei != 0) {
            (bool ok,) = liquidationTreasury.call{value:seizeWei}("");
            if (!ok) revert TransferFailed();
            emit LiquidationTriggered(loanId,seizeWei,remainingBadDebtMicro,true);
        }
        emit LoanRepaid(loanId,repaidMicro,remainingBadDebtMicro,true);
    }

    function aprBps() external view returns(uint256) { return rewardPool.aprBps(); }
    function rewardReserve() external view returns(uint256) { return rewardPool.rewardReserve(); }
    function rewardLiability() external view returns(uint256 claimable) { (claimable,)=rewardPool.preview(address(this)); }
    function totalRewardsFunded() external view returns(uint256) { return rewardPool.totalRewardsFunded(); }
    function totalRewardsClaimed() external view returns(uint256 claimed) { (,,,,claimed)=rewardPool.accounts(address(this)); }
    function previewRewards() public view onChain returns(uint256 claimable,uint256 carry) { return rewardPool.preview(address(this)); }
    function checkpointRewards() external onChain nonReentrant { rewardPool.checkpointVault(); }
    /// Compatibility entry: the entire donation is forwarded to the shared pool, never collateral.
    function topUpReserve() external payable onChain nonReentrant {
        if(msg.value==0) revert InvalidRewardTerms();
        rewardPool.topUpReserve{value:msg.value}();emit RewardReserveFunded(msg.sender,msg.value);
    }
    /// Pool pays the owner directly. This event binds the payout to the reviewed vault claim.
    function claimRewards() external onChain onlyOwner nonReentrant returns(uint256 paid) {
        paid=rewardPool.claim();emit RewardClaimed(owner(),paid);
    }
}
