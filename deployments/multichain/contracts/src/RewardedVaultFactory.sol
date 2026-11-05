// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {Create2} from "@openzeppelin/contracts/utils/Create2.sol";
import {RewardedCollateralVault} from "./RewardedCollateralVault.sol";
import {NativeRewardPool} from "./NativeRewardPool.sol";
import {VaultEvents} from "./VaultEvents.sol";

contract RewardedVaultFactory is VaultEvents {
    address public immutable settlementSigner;
    address payable public immutable liquidationTreasury;
    address public immutable rewardManager;
    uint256 public immutable initialAprBps;
    NativeRewardPool public immutable rewardPool;
    mapping(address => address) public vaultOf;
    error UnsupportedChain();
    error AlreadyCreated();
    constructor(address signer, address payable treasury, address manager, uint256 rateBps) {
        if (block.chainid != 6281971 && block.chainid != 31337) revert UnsupportedChain();
        require(signer != address(0) && treasury != address(0), "Zero address");
        require(manager != address(0) && rateBps <= 10_000, "Invalid reward terms");
        rewardManager=manager; initialAprBps=rateBps;
        rewardPool=new NativeRewardPool(manager,rateBps);
        settlementSigner=signer; liquidationTreasury=treasury;
    }
    function createVault() external returns(address vault) {
        if (vaultOf[msg.sender] != address(0)) revert AlreadyCreated();
        vault=address(new RewardedCollateralVault{salt:bytes32(uint256(uint160(msg.sender)))}(
            msg.sender,settlementSigner,liquidationTreasury,rewardPool));
        rewardPool.registerVault(vault,msg.sender);
        vaultOf[msg.sender]=vault;
        emit VaultCreated(msg.sender,vault);
    }
    function predict(address user) external view returns(address) {
        return Create2.computeAddress(bytes32(uint256(uint160(user))),keccak256(abi.encodePacked(
            type(RewardedCollateralVault).creationCode,abi.encode(user,settlementSigner,liquidationTreasury,rewardPool))));
    }
}
