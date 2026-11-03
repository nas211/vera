// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

interface VaultEvents {
    event VaultCreated(address indexed owner, address indexed vault);
    event CollateralDeposited(address indexed owner, uint256 amount, uint256 total);
    event BorrowRequested(bytes32 indexed loanId, uint256 principalMicro, uint256 nonce);
    event LoanIssued(bytes32 indexed loanId, uint256 principalMicro, bool simulated);
    event AgentAuthorized(address indexed agent, uint256 revision, uint256 expiresAt);
    event AgentRevoked(address indexed agent, uint256 revision);
    event TradingStarted(bytes32 indexed loanId, uint256 revision);
    event TradingStopped(bytes32 indexed loanId);
    event LoanRepaid(bytes32 indexed loanId, uint256 repaidMicro, uint256 badDebtMicro, bool simulated);
    event CollateralWithdrawn(address indexed owner, uint256 amount, uint256 remaining);
    event LiquidationTriggered(bytes32 indexed loanId, uint256 testDogeSeized, uint256 badDebtMicro, bool simulated);
}
