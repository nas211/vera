// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Proposed interface only: no implementation, deployment or audit claim.
/// @dev One owner per vault. Checkpoint before principal/rate/reserve mutations.
/// Reward funds are accounted separately from locked collateral principal.
interface IRewardedCollateralVault {
    event CollateralDeposited(address indexed owner, uint256 amount);
    event CollateralWithdrawn(address indexed owner, uint256 amount);
    event RewardsFunded(address indexed funder, uint256 amount);
    event RewardClaimed(address indexed owner, uint256 amount);
    event RewardsCheckpointed(uint256 timestamp, uint256 newlyAccrued);
    function owner() external view returns (address);
    function collateralPrincipal() external view returns (uint256);
    function rewardReserve() external view returns (uint256);
    function accruedRewardLiability() external view returns (uint256);
    function claimableRewards() external view returns (uint256);
    function rewardAprBps() external view returns (uint256);
    function checkpoint() external;
    function depositCollateral() external payable;
    function withdrawCollateral(uint256 amount) external;
    function fundRewards() external payable;
    function claimRewards() external returns (uint256 paid);
    // Existing loan locking/settlement semantics must be designed and tested
    // separately. This interface intentionally grants no borrower/keeper power
    // to unlock collateral, mint USD, or seize funds.
}
