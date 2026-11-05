// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// Shared, prefunded test-DOGE rewards. Holds no collateral and has no admin withdrawal.
/// Factory-registered vaults checkpoint principal; only their owner can receive rewards.
contract NativeRewardPool is ReentrancyGuard {
    uint256 public constant DENOMINATOR = 10_000 * 365 days;
    address public immutable factory;
    address public immutable rewardManager;
    uint256 public immutable expectedChainId;
    uint256 public aprBps;
    uint256 public rewardReserve;
    uint256 public totalRewardsFunded;
    uint256 public totalRewardsClaimed;
    uint256 public totalPrincipal;
    uint256 public rewardIndex;
    uint256 public liabilityNumerator;
    uint256 public lastCheckpoint;
    struct Account { address owner; uint256 principal; uint256 index; uint256 numerator; uint256 claimed; }
    mapping(address => Account) public accounts;
    error InvalidPoolAction();
    error RewardTransferRejected();
    event PoolFunded(address indexed funder, uint256 amount);
    event RewardRateChanged(uint256 previousBps, uint256 nextBps);
    event VaultRegistered(address indexed vault, address indexed owner);
    event PoolRewardPaid(address indexed vault, address indexed owner, uint256 amount);
    modifier onChain() { if(block.chainid!=expectedChainId) revert InvalidPoolAction(); _; }
    constructor(address manager,uint256 rateBps) {
        if((block.chainid!=6281971 && block.chainid!=31337)||manager==address(0)||rateBps>10_000) revert InvalidPoolAction();
        factory=msg.sender;rewardManager=manager;expectedChainId=block.chainid;
        aprBps=rateBps;lastCheckpoint=block.timestamp;
    }
    receive() external payable { revert InvalidPoolAction(); }
    function registerVault(address vault,address owner) external onChain {
        if(msg.sender!=factory||vault==address(0)||owner==address(0)||accounts[vault].owner!=address(0)) revert InvalidPoolAction();
        _checkpoint();accounts[vault]=Account(owner,0,rewardIndex,0,0);emit VaultRegistered(vault,owner);
    }
    /// Index units are basis-point-seconds. Fractional liabilities use the same exact denominator.
    /// Scarcity caps the common index for everyone, independent of claim order.
    function previewIndex() public view onChain returns(uint256 index,uint256 liability) {
        index=rewardIndex;liability=liabilityNumerator;
        if(totalPrincipal==0) return(index,liability);
        uint256 delta=aprBps*(block.timestamp-lastCheckpoint);
        uint256 affordable=(rewardReserve*DENOMINATOR-liability)/totalPrincipal;
        if(delta>affordable) delta=affordable;
        return(index+delta,liability+totalPrincipal*delta);
    }
    function preview(address vault) public view returns(uint256 claimable,uint256 carry) {
        Account storage a=accounts[vault];
        (uint256 index,)=previewIndex();
        uint256 numerator=a.numerator+a.principal*(index-a.index);
        return(numerator/DENOMINATOR,numerator%DENOMINATOR);
    }
    function availableReserve() external view returns(uint256) {
        (,uint256 liability)=previewIndex();return(rewardReserve*DENOMINATOR-liability)/DENOMINATOR;
    }
    function _checkpoint() private {
        (rewardIndex,liabilityNumerator)=previewIndex();lastCheckpoint=block.timestamp;
    }
    function _account() private returns(Account storage a) {
        a=accounts[msg.sender];if(a.owner==address(0)) revert InvalidPoolAction();
        _checkpoint();a.numerator+=a.principal*(rewardIndex-a.index);a.index=rewardIndex;
    }
    function updatePrincipal(uint256 principal) external onChain nonReentrant {
        Account storage a=_account();totalPrincipal=totalPrincipal-a.principal+principal;a.principal=principal;
    }
    function checkpointVault() external onChain nonReentrant { _account(); }
    function topUpReserve() external payable onChain nonReentrant {
        if(msg.value==0) revert InvalidPoolAction();
        // Unfunded time is checkpointed before new money arrives; it never earns retroactively.
        _checkpoint();rewardReserve+=msg.value;totalRewardsFunded+=msg.value;emit PoolFunded(msg.sender,msg.value);
    }
    function setRewardRate(uint256 nextBps) external onChain nonReentrant {
        if(msg.sender!=rewardManager||nextBps>10_000) revert InvalidPoolAction();
        _checkpoint();emit RewardRateChanged(aprBps,nextBps);aprBps=nextBps;
    }
    function claim() external onChain nonReentrant returns(uint256 paid) {
        Account storage a=_account();paid=a.numerator/DENOMINATOR;
        if(paid==0) revert InvalidPoolAction();
        a.numerator-=paid*DENOMINATOR;liabilityNumerator-=paid*DENOMINATOR;
        a.claimed+=paid;rewardReserve-=paid;totalRewardsClaimed+=paid;
        (bool ok,)=payable(a.owner).call{value:paid}("");if(!ok) revert RewardTransferRejected();
        emit PoolRewardPaid(msg.sender,a.owner,paid);
    }
}
