// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {VelaVault} from "./VelaVault.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/// @notice Replacement deployment, not an upgrade of the immutable V1 vault.
/// Signed prices travel with START; settlement updates its price atomically.
/// No periodic oracle transactions are needed for V2.
contract VelaVaultV2 is VelaVault, EIP712 {
    bytes32 public constant START_QUOTE_TYPEHASH = keccak256(
        "StartQuote(address user,uint256 strategyId,uint256 dogeIn,uint256 price,uint256 issuedAt,uint256 deadline,uint256 nonce)"
    );
    event PoolWithdrawn(address indexed recipient, uint256 doge);

    constructor(address initialOwner, address initialOperator)
        VelaVault(initialOwner, initialOperator) EIP712("VelaVault", "2") {}

    function startTradingWithQuote(uint256 strategyId, uint256 price, uint256 issuedAt,
        uint256 deadline, uint256 nonce, bytes calldata signature)
        external payable nonReentrant whenNotPaused {
        require(issuedAt <= block.timestamp && block.timestamp - issuedAt <= MAX_PRICE_AGE, "stale quote");
        require(deadline >= block.timestamp && deadline <= issuedAt + MAX_PRICE_AGE, "expired quote");
        require(nonce == sessions[msg.sender].nonce + 1, "quote nonce");
        bytes32 digest = _hashTypedDataV4(keccak256(abi.encode(START_QUOTE_TYPEHASH,
            msg.sender, strategyId, msg.value, price, issuedAt, deadline, nonce)));
        require(ECDSA.recover(digest, signature) == operator, "invalid quote signer");
        require(price > 0 && price <= 1e18, "invalid price");
        require(strategyEnabled[strategyId], "unknown strategy");
        require(sessions[msg.sender].state == State.Idle, "already started");
        require(msg.value > 0 && msg.value <= 1e30, "invalid deposit");
        uint256 credit = Math.mulDiv(msg.value, price, CONVERSION_SCALE);
        require(credit > 0 && credit <= uint256(uint128(type(int128).max)), "invalid credit");
        sessions[msg.sender] = Session(State.Active, strategyId, msg.value, credit, 0, nonce);
        totalPrincipal += msg.value;
        activeUsers++;
        wdoge.deposit{value: msg.value}();
        tusdc.mint(address(this), credit);
        emit TradingStarted(msg.sender, strategyId, msg.value, credit, price);
    }

    function settleAtPrice(address user, int256 pnlUsdc, uint256 price, uint256 priceTimestamp)
        external onlyOperator nonReentrant {
        require(priceTimestamp <= block.timestamp && block.timestamp - priceTimestamp <= MAX_PRICE_AGE, "stale price");
        require(price > 0 && price <= 1e18, "invalid price");
        Session storage s = sessions[user];
        require(s.state == State.Stopping, "not stopping");
        require(pnlUsdc == s.loggedPnl, "unlogged pnl");
        uint256 credit = s.usdcCredited;
        int256 finalCredit = int256(credit) + pnlUsdc;
        uint256 balance = finalCredit > 0 ? uint256(finalCredit) : 0;
        uint256 available = wdoge.balanceOf(address(this)) - totalPrincipal + s.dogeIn;
        uint256 requested = Math.mulDiv(balance, CONVERSION_SCALE, price);
        uint256 dogeOut = Math.min(requested, available);
        totalPrincipal -= s.dogeIn;
        activeUsers--;
        s.state = State.Idle;
        s.usdcCredited = 0;
        s.dogeIn = 0;
        tusdc.burn(address(this), credit);
        if (dogeOut < requested) emit PayoutCapped(user, requested, dogeOut);
        if (dogeOut > 0) {
            wdoge.withdraw(dogeOut);
            (bool ok,) = user.call{value: dogeOut}("");
            require(ok, "DOGE transfer failed");
        }
        emit Settled(user, pnlUsdc, dogeOut, price);
    }

    /// Owner may reclaim reserves only after pausing and completing every session.
    function withdrawPool(uint256 amount, address payable recipient)
        external onlyOwner whenPaused nonReentrant {
        require(activeUsers == 0 && totalPrincipal == 0, "sessions outstanding");
        require(recipient != address(0) && recipient != address(this), "invalid recipient");
        require(amount > 0 && amount <= wdoge.balanceOf(address(this)), "invalid withdrawal");
        wdoge.withdraw(amount);
        (bool ok,) = recipient.call{value: amount}("");
        require(ok, "DOGE transfer failed");
        emit PoolWithdrawn(recipient, amount);
    }
}
