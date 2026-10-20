// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {WDOGE} from "./WDOGE.sol";
import {TUSDC} from "./TUSDC.sol";

contract VelaVault is Ownable, Pausable, ReentrancyGuard {
    enum State { Idle, Active, Stopping }
    struct Session {
        State state;
        uint256 strategyId;
        uint256 dogeIn;
        uint256 usdcCredited;
        int256 loggedPnl;
        uint256 nonce;
    }
    WDOGE public immutable wdoge;
    TUSDC public immutable tusdc;
    address public operator;
    uint256 public dogeUsd;
    uint256 public priceUpdatedAt;
    uint256 public totalPrincipal;
    uint256 public activeUsers;
    uint256 public constant MAX_PRICE_AGE = 120;
    uint256 public constant PRICE_SCALE = 1e8;
    // 18 DOGE decimals + 8 price decimals - 6 USDC decimals.
    uint256 public constant CONVERSION_SCALE = 1e20;
    mapping(address => Session) public sessions;
    mapping(bytes32 => bool) public tradeLogged;
    mapping(uint256 => bool) public strategyEnabled;

    event TradingStarted(address indexed user, uint256 strategyId, uint256 dogeIn, uint256 usdcCredited, uint256 price);
    event StopRequested(address indexed user);
    event Settled(address indexed user, int256 pnl, uint256 dogeOut, uint256 price);
    event TradeLogged(address indexed user, bytes32 indexed tradeId, string symbol, uint8 side, uint256 size, uint256 entryPrice, uint256 exitPrice, int256 pnl);
    event PriceUpdated(uint256 price, uint256 timestamp);
    event PoolFunded(address indexed sender, uint256 doge);
    event PayoutCapped(address indexed user, uint256 requested, uint256 paid);
    event OperatorChanged(address indexed operator);
    event StrategyChanged(uint256 indexed strategyId, bool enabled);

    constructor(address initialOwner, address initialOperator) Ownable(initialOwner) {
        require(block.chainid == 6281971 || block.chainid == 31337, "testnet only");
        require(initialOperator != address(0), "zero operator");
        operator = initialOperator;
        wdoge = new WDOGE();
        tusdc = new TUSDC();
        strategyEnabled[1] = true;
        strategyEnabled[2] = true;
        strategyEnabled[3] = true;
    }

    modifier onlyOperator() { require(msg.sender == operator, "operator only"); _; }
    modifier freshPrice() {
        require(dogeUsd > 0 && block.timestamp - priceUpdatedAt <= MAX_PRICE_AGE, "stale price");
        _;
    }

    function setOperator(address next) external onlyOwner {
        require(next != address(0), "zero operator");
        operator = next;
        emit OperatorChanged(next);
    }

    function setStrategy(uint256 id, bool enabled) external onlyOwner {
        require(id > 0, "zero strategy");
        strategyEnabled[id] = enabled;
        emit StrategyChanged(id, enabled);
    }

    // Pausing blocks entries, while stop, trade close logs, and settlement remain available.
    function pause() external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    function updatePrice(uint256 price) external onlyOperator {
        require(price > 0 && price <= 1e18, "invalid price");
        dogeUsd = price;
        priceUpdatedAt = block.timestamp;
        emit PriceUpdated(price, block.timestamp);
    }

    function fundPool() external payable nonReentrant {
        require(msg.value > 0, "zero funding");
        wdoge.deposit{value: msg.value}();
        emit PoolFunded(msg.sender, msg.value);
    }

    function startTrading(uint256 strategyId) external payable nonReentrant whenNotPaused freshPrice {
        require(strategyEnabled[strategyId], "unknown strategy");
        require(sessions[msg.sender].state == State.Idle, "already started");
        require(msg.value > 0 && msg.value <= 1e30, "invalid deposit");
        uint256 credited = Math.mulDiv(msg.value, dogeUsd, CONVERSION_SCALE);
        require(credited > 0 && credited <= uint256(uint128(type(int128).max)), "invalid credit");
        uint256 nonce = sessions[msg.sender].nonce + 1;
        sessions[msg.sender] = Session(State.Active, strategyId, msg.value, credited, 0, nonce);
        totalPrincipal += msg.value;
        activeUsers++;
        wdoge.deposit{value: msg.value}();
        tusdc.mint(address(this), credited);
        emit TradingStarted(msg.sender, strategyId, msg.value, credited, dogeUsd);
    }

    function stopTrading() external nonReentrant {
        require(sessions[msg.sender].state == State.Active, "not active");
        sessions[msg.sender].state = State.Stopping;
        emit StopRequested(msg.sender);
    }

    function logTrade(address user, bytes32 tradeId, string calldata symbol, uint8 side,
        uint256 size, uint256 entryPrice, uint256 exitPrice, int256 pnl) external onlyOperator {
        Session storage s = sessions[user];
        require(s.state != State.Idle, "not active");
        require(!tradeLogged[tradeId] && tradeId != bytes32(0), "duplicate trade");
        require(bytes(symbol).length > 0 && bytes(symbol).length <= 32 && side <= 1, "invalid trade");
        require(size > 0 && entryPrice > 0 && exitPrice > 0, "invalid fill");
        require(pnl >= type(int128).min && pnl <= type(int128).max, "invalid pnl");
        int256 nextPnl = s.loggedPnl + pnl;
        require(nextPnl >= type(int128).min && nextPnl <= type(int128).max, "pnl overflow");
        s.loggedPnl = nextPnl;
        tradeLogged[tradeId] = true;
        emit TradeLogged(user, tradeId, symbol, side, size, entryPrice, exitPrice, pnl);
    }

    function settle(address user, int256 pnlUsdc, uint256 price) external onlyOperator nonReentrant freshPrice {
        Session storage s = sessions[user];
        require(s.state == State.Stopping, "not stopping");
        require(price == dogeUsd, "price mismatch");
        require(pnlUsdc == s.loggedPnl, "unlogged pnl");
        uint256 credit = s.usdcCredited;
        int256 finalCredit = int256(credit) + pnlUsdc;
        uint256 balance = finalCredit > 0 ? uint256(finalCredit) : 0;
        // Other users' deposited DOGE is reserved; profits cannot consume it.
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

    receive() external payable {
        // Only the guarded settlement path can ask our wrapper to send native DOGE.
        require(msg.sender == address(wdoge), "use fundPool");
    }
}
