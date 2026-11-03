// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {Create2} from "@openzeppelin/contracts/utils/Create2.sol";
import {DogeCollateralVault} from "./DogeCollateralVault.sol";
import {VaultEvents} from "./VaultEvents.sol";

contract VaultFactory is VaultEvents {
    address public immutable settlementSigner;
    address payable public immutable liquidationTreasury;
    mapping(address => address) public vaultOf;
    error UnsupportedChain();
    error AlreadyCreated();
    constructor(address signer, address payable treasury) {
        if (block.chainid != 6281971 && block.chainid != 31337) revert UnsupportedChain();
        require(signer != address(0) && treasury != address(0), "Zero address");
        settlementSigner=signer; liquidationTreasury=treasury;
    }
    function createVault() external returns(address vault) {
        if (vaultOf[msg.sender] != address(0)) revert AlreadyCreated();
        vault=address(new DogeCollateralVault{salt:bytes32(uint256(uint160(msg.sender)))}(
            msg.sender,settlementSigner,liquidationTreasury));
        vaultOf[msg.sender]=vault;
        emit VaultCreated(msg.sender,vault);
    }
    function predict(address user) external view returns(address) {
        return Create2.computeAddress(bytes32(uint256(uint160(user))),keccak256(abi.encodePacked(
            type(DogeCollateralVault).creationCode,abi.encode(user,settlementSigner,liquidationTreasury))));
    }
}
