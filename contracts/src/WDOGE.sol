// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// Dedicated showcase wrapper; not DogeOS's canonical/native ERC20.
contract WDOGE is ERC20, ReentrancyGuard {
    constructor() ERC20("Vela Wrapped Test DOGE", "vWDOGE") {}

    function deposit() public payable nonReentrant {
        require(msg.value > 0, "zero deposit");
        _mint(msg.sender, msg.value);
    }

    function withdraw(uint256 amount) external nonReentrant {
        require(amount > 0, "zero withdrawal");
        _burn(msg.sender, amount);
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok, "DOGE transfer failed");
    }

    receive() external payable {
        deposit();
    }
}
