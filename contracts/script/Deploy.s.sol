// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {VelaVault} from "../src/VelaVault.sol";

interface VmDeploy {
    function envUint(string calldata) external returns (uint256);
    function addr(uint256) external returns (address);
    function startBroadcast(uint256) external;
    function stopBroadcast() external;
}

/// The post-receipt Python deployment helper writes verified addresses to config.
contract Deploy {
    VmDeploy constant vm = VmDeploy(address(uint160(uint256(keccak256("hevm cheat code")))));
    function run() external returns (VelaVault vault) {
        require(block.chainid == 6281971, "DogeOS testnet only");
        uint256 key = vm.envUint("VELA_OPERATOR_PRIVATE_KEY");
        address operator = vm.addr(key);
        vm.startBroadcast(key);
        vault = new VelaVault(operator, operator);
        vm.stopBroadcast();
    }
}
