// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

library AgentPermissions {
    uint256 internal constant MAX_LOSS_MICRO = 2_000_000;
    struct Limits {
        uint128 allocation;
        uint128 positionSize;
        uint64 expiresAt;
        uint32 maxLoss;
        uint8 leverage;
        uint8 markets; // BTC=1, ETH=2, SOL=4, DOGE=8 (only if venue supports it).
        bool autoReduce;
    }
    error InvalidLimits();
    function validate(Limits calldata p) internal view {
        if (p.allocation == 0 || p.allocation > 1_000_000e6 || p.positionSize == 0 ||
            p.positionSize > uint256(p.allocation) * p.leverage || p.expiresAt <= block.timestamp ||
            p.expiresAt > block.timestamp + 30 days || p.maxLoss == 0 || p.maxLoss > MAX_LOSS_MICRO ||
            p.leverage == 0 || p.leverage > 3 || p.markets == 0 || p.markets > 15) revert InvalidLimits();
    }
}
