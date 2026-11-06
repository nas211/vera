// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// Faucet-funded DogeOS TESTNET market. Not an upgrade to the existing demo vaults.
/// Native collateral, unwithdrawn loan proceeds and available pool liquidity are
/// separate liabilities. No paper P&L, USD ledger credit or reward reserve is money here.
contract TestDogeLending is Ownable, ReentrancyGuard {
    uint256 public constant CHAIN_ID = 6281971;
    uint256 public constant MAX_LTV_BPS = 7000;
    uint256 public constant RISK_LTV_BPS = 8000;
    uint256 public constant APR_BPS = 500;
    uint256 private constant DENOMINATOR = 10000 * 365 days;
    uint256 public immutable deployedChainId;
    bool public borrowingPaused;
    uint256 public liquidity;
    uint256 public totalCollateral;
    uint256 public totalAvailable;
    uint256 public totalPrincipal;
    struct Position {
        uint256 collateral;
        uint256 principal;
        uint256 interest;
        uint256 interestRemainder;
        uint256 updatedAt;
        uint256 available;
        uint256 revision;
    }
    mapping(address => Position) private positions;
    error WrongChain();
    error InvalidAmount();
    error LimitExceeded();
    error InsufficientLiquidity();
    error CollateralLocked();
    error TransferFailed();
    error Paused();
    error StaleRequest();
    event Funded(address indexed sender, uint256 amount);
    event LiquidityWithdrawn(address indexed receiver, uint256 amount);
    event CollateralAdded(address indexed account, uint256 amount);
    event CollateralWithdrawn(address indexed account, uint256 amount);
    event Borrowed(address indexed account, uint256 amount, uint256 available);
    event ProceedsWithdrawn(address indexed account, uint256 amount);
    event Repaid(address indexed account, address indexed payer, uint256 interest, uint256 principal, uint8 source);
    event BorrowingPaused(bool paused);
    modifier onChain() {
        if (block.chainid != deployedChainId) revert WrongChain();
        _;
    }
    modifier fresh(uint256 revision, uint256 deadline) {
        if (deadline < block.timestamp || deadline > block.timestamp + 15 minutes || positions[msg.sender].revision != revision) revert StaleRequest();
        _;
    }
    constructor(address manager) Ownable(manager) {
        if (block.chainid != CHAIN_ID && block.chainid != 31337) revert WrongChain();
        deployedChainId = block.chainid;
    }
    // Pool funding is explicitly a project contribution, not a public deposit/share product.
    function fund() external payable onChain nonReentrant {
        if (msg.value == 0) revert InvalidAmount();
        liquidity += msg.value;
        emit Funded(msg.sender, msg.value);
    }
    function withdrawLiquidity(uint256 amount) external onlyOwner onChain nonReentrant {
        if (amount == 0 || amount > liquidity) revert InsufficientLiquidity();
        liquidity -= amount;
        _send(msg.sender, amount);
        emit LiquidityWithdrawn(msg.sender, amount);
    }
    function setBorrowingPaused(bool paused) external onlyOwner onChain {
        borrowingPaused = paused;
        emit BorrowingPaused(paused);
    }
    function _interest(Position memory p) private view returns (uint256 interest, uint256 remainder) {
        // A conservative cap keeps every product in the exact uint256 domain.
        uint256 elapsed = p.updatedAt == 0 ? 0 : block.timestamp - p.updatedAt;
        uint256 numerator = p.principal * APR_BPS * elapsed + p.interestRemainder;
        return (p.interest + numerator / DENOMINATOR, numerator % DENOMINATOR);
    }
    function position(address account) external view returns (Position memory p) {
        p = positions[account];
        (p.interest, p.interestRemainder) = _interest(p);
    }
    function debt(address account) public view returns (uint256) {
        Position memory p = positions[account];
        (uint256 interest,) = _interest(p);
        return p.principal + interest;
    }
    function capacity(address account) public view returns (uint256) {
        if (borrowingPaused) return 0;
        uint256 maximum = positions[account].collateral * MAX_LTV_BPS / 10000;
        uint256 owed = debt(account);
        uint256 remaining = maximum > owed ? maximum - owed : 0;
        return remaining < liquidity ? remaining : liquidity;
    }
    function _checkpoint(address account) private returns (Position storage p) {
        p = positions[account];
        (p.interest, p.interestRemainder) = _interest(p);
        p.updatedAt = block.timestamp;
        p.revision++;
    }
    function deposit(uint256 revision, uint256 deadline) external payable onChain nonReentrant fresh(revision, deadline) {
        if (msg.value == 0 || positions[msg.sender].collateral + msg.value > 1e36) revert InvalidAmount();
        Position storage p = _checkpoint(msg.sender);
        p.collateral += msg.value;
        totalCollateral += msg.value;
        emit CollateralAdded(msg.sender, msg.value);
    }
    function borrow(uint256 amount, uint256 revision, uint256 deadline) external onChain nonReentrant fresh(revision, deadline) {
        if (borrowingPaused) revert Paused();
        if (amount == 0) revert InvalidAmount();
        if (amount > liquidity) revert InsufficientLiquidity();
        if (amount > capacity(msg.sender)) revert LimitExceeded();
        Position storage p = _checkpoint(msg.sender);
        p.principal += amount;
        p.available += amount;
        totalPrincipal += amount;
        totalAvailable += amount;
        liquidity -= amount;
        emit Borrowed(msg.sender, amount, p.available);
    }
    function withdrawProceeds(uint256 amount, uint256 revision, uint256 deadline) external onChain nonReentrant fresh(revision, deadline) {
        if (amount == 0 || amount > positions[msg.sender].available) revert InvalidAmount();
        Position storage p = _checkpoint(msg.sender);
        p.available -= amount;
        totalAvailable -= amount;
        _send(msg.sender, amount);
        emit ProceedsWithdrawn(msg.sender, amount);
    }
    function withdrawCollateral(uint256 amount, uint256 revision, uint256 deadline) external onChain nonReentrant fresh(revision, deadline) {
        if (debt(msg.sender) != 0) revert CollateralLocked();
        if (amount == 0 || amount > positions[msg.sender].collateral) revert InvalidAmount();
        Position storage p = _checkpoint(msg.sender);
        p.collateral -= amount;
        totalCollateral -= amount;
        _send(msg.sender, amount);
        emit CollateralWithdrawn(msg.sender, amount);
    }
    function _repay(address account, uint256 maximum, uint8 source) private returns (uint256 paid) {
        Position storage p = _checkpoint(account);
        uint256 owed = p.principal + p.interest;
        if (maximum == 0 || owed == 0) revert InvalidAmount();
        paid = maximum < owed ? maximum : owed;
        uint256 interestPaid = paid < p.interest ? paid : p.interest;
        uint256 principalPaid = paid - interestPaid;
        p.interest -= interestPaid;
        p.principal -= principalPaid;
        totalPrincipal -= principalPaid;
        if (p.principal == 0 && p.interest == 0) p.interestRemainder = 0;
        liquidity += paid;
        emit Repaid(account, msg.sender, interestPaid, principalPaid, source);
    }
    function repayAvailable(uint256 maximum, uint256 revision, uint256 deadline) external onChain nonReentrant fresh(revision, deadline) returns (uint256 paid) {
        if (maximum > positions[msg.sender].available) revert InvalidAmount();
        paid = _repay(msg.sender, maximum, 0);
        positions[msg.sender].available -= paid;
        totalAvailable -= paid;
    }
    function repay(uint256 revision, uint256 deadline) external payable onChain nonReentrant fresh(revision, deadline) returns (uint256 paid) {
        paid = _repay(msg.sender, msg.value, 1);
        if (msg.value > paid) _send(msg.sender, msg.value - paid);
    }
    /// A conversion adapter may repay only with DOGE it actually delivers. No certificates.
    function repayFor(address account) external payable onChain nonReentrant returns (uint256 paid) {
        paid = _repay(account, msg.value, 2);
        if (msg.value > paid) _send(msg.sender, msg.value - paid);
    }
    /// Explicit owner settlement, including while paused. Never an automatic liquidation.
    function repayCollateral(uint256 maximum, uint256 revision, uint256 deadline) external onChain nonReentrant fresh(revision, deadline) returns (uint256 paid) {
        if (maximum > positions[msg.sender].collateral) revert InvalidAmount();
        paid = _repay(msg.sender, maximum, 3);
        positions[msg.sender].collateral -= paid;
        totalCollateral -= paid;
    }
    function _send(address recipient, uint256 amount) private {
        (bool ok,) = payable(recipient).call{value: amount}("");
        if (!ok) revert TransferFailed();
    }
    // No receive(): an ordinary transfer must not be mistaken for a deposit/repayment.
}
