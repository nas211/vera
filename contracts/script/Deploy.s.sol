// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {VelaVaultV2} from "../src/VelaVaultV2.sol";

interface VmDeploy {
    function envUint(string calldata) external returns (uint256);
    function addr(uint256) external returns (address);
    function startBroadcast(uint256) external;
    function stopBroadcast() external;
}

/// Local development only. Public DogeOS deployment must use vela.deploy,
/// which includes data/finality fees and an explicit deployment fee budget.
contract Deploy {
    VmDeploy constant vm = VmDeploy(address(uint160(uint256(keccak256("hevm cheat code")))));
    function run() external returns (VelaVaultV2 vault) {
        require(block.chainid == 31337, "Use fee-guarded Python deploy for DogeOS");
        uint256 key = vm.envUint("VELA_OPERATOR_PRIVATE_KEY");
        address operator = vm.addr(key);
        vm.startBroadcast(key);
        vault = new VelaVaultV2(operator, operator);
        vm.stopBroadcast();
    }
}
