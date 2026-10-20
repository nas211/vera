// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// Test accounting token, held by the vault. No real USDC or redemption promise.
contract TUSDC is ERC20 {
    address public immutable vault;
    constructor() ERC20("Vela Test USDC", "TUSDC") { vault = msg.sender; }
    modifier onlyVault() { require(msg.sender == vault, "vault only"); _; }
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external onlyVault { _mint(to, amount); }
    function burn(address from, uint256 amount) external onlyVault { _burn(from, amount); }
}
