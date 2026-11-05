// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title VelaRewardVault — CANDIDATE (Stage 2). NOT compiled here, NOT audited, NOT deployed.
 * @notice NEW isolated reward-enabled vault for native test DOGE on DogeOS testnet.
 *         It does not attach to, migrate, or read existing Vela vaults (they lack checkpoint hooks).
 *         Mirrors src/lib/rewardModel.ts, which is the executable test oracle.
 *
 * Rules:
 *  - Rewards are paid ONLY from this vault's separately funded reserve (never principal,
 *    new deposits or borrowed funds). Principal and reserve are tracked in separate counters.
 *  - Simple APR in basis points (500 = 5%). No compounding. Labeled testnet demo incentives.
 *  - _checkpoint() runs before every deposit, withdrawal, seizure, rate change, top-up and claim.
 *  - Accrual is capped at (reserveBalance - liabilities); unfunded time is never backfilled.
 *  - rewardDebt and per-checkpoint liabilities both use ceil rounding so
 *    sum(owed) <= liabilities <= reserveBalance always (sub-wei dust stays reserved).
 *  - Principal protection: after every state change, this contract's native balance must cover
 *    totalPrincipal + reserveBalance. Any path that would dip into principal reverts.
 *
 * Overflow note: pay <= totalPrincipal * aprBps * dt / (BPS * YEAR), so accPerShare grows by at most
 * ~ACC_PRECISION * (aprBps / BPS) per year of elapsed time. principal * accPerShare stays far below
 * 2^256 for any realistic test-DOGE supply and horizon.
 */
contract VelaRewardVault {
    uint256 private constant BPS = 10_000;
    uint256 private constant YEAR = 365 days;
    uint256 private constant ACC_PRECISION = 1e36;

    struct UserPosition { uint256 principal; uint256 rewardDebt; uint256 owed; uint256 claimed; }

    address public immutable owner;
    uint256 public immutable expectedChainId;
    /// @notice Factory that created this vault (address(0) if deployed directly).
    address public immutable factory;

    uint256 public aprBps;
    uint256 public totalPrincipal;
    uint256 public reserveBalance;
    uint256 public liabilities;
    uint256 public accPerShare;
    uint64  public lastCheckpoint;
    uint256 public totalFunded;
    uint256 public totalClaimed;
    mapping(address => UserPosition) public positions;

    bool private _locked;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);
    event Seized(address indexed user, uint256 amount, address indexed to);
    event RateChanged(uint256 oldBps, uint256 newBps);
    event ReserveToppedUp(address indexed from, uint256 amount);
    event Claimed(address indexed user, uint256 amount);
    event Checkpointed(uint256 due, uint256 paid, uint64 at);

    error OnlyOwner();
    error WrongChain();
    error ZeroAmount();
    error ZeroAddress();
    error Insufficient();
    error NothingToClaim();
    error Reentrancy();
    error TransferFailed();
    error RateOutOfRange();
    error ReserveShortfall();
    error PrincipalUnprotected();
    error DirectTransferRejected();

    modifier onlyOwner() { if (msg.sender != owner) revert OnlyOwner(); _; }
    modifier onChain() { if (block.chainid != expectedChainId) revert WrongChain(); _; }
    modifier nonReentrant() { if (_locked) revert Reentrancy(); _locked = true; _; _locked = false; }
    /// @dev Runs AFTER the body: principal + reserve must always be fully backed by native balance.
    modifier protectsPrincipal() {
        _;
        if (address(this).balance < totalPrincipal + reserveBalance) revert PrincipalUnprotected();
        if (liabilities > reserveBalance) revert ReserveShortfall();
    }

    /// @param _owner explicit owner (the factory passes the requested owner, never itself)
    constructor(address _owner, uint256 _aprBps, uint256 _expectedChainId) {
        if (_owner == address(0)) revert ZeroAddress();
        if (_aprBps > BPS) revert RateOutOfRange();
        if (block.chainid != _expectedChainId) revert WrongChain();
        owner = _owner;
        factory = msg.sender == _owner ? address(0) : msg.sender;
        aprBps = _aprBps;
        expectedChainId = _expectedChainId;
        lastCheckpoint = uint64(block.timestamp);
    }

    // ---------------------------------------------------------------- accounting
    function _checkpoint() internal {
        uint256 dt = block.timestamp - lastCheckpoint;
        if (dt == 0) return;
        if (totalPrincipal == 0 || aprBps == 0) { lastCheckpoint = uint64(block.timestamp); return; }
        uint256 due = (totalPrincipal * aprBps * dt) / (BPS * YEAR);
        uint256 available = reserveBalance - liabilities;
        uint256 pay = due < available ? due : available;
        if (pay > 0) {
            uint256 accDelta = (pay * ACC_PRECISION) / totalPrincipal;
            accPerShare += accDelta;
            // CEIL, not floor: users settle on floor(principal * accPerShare / PREC) (< 1 wei loss per
            // settlement) while a floored liability loses < 1 wei per checkpoint, letting sum(owed)
            // exceed liabilities and making claim() revert with ReserveShortfall. Ceil <= pay because
            // accDelta * totalPrincipal <= pay * PREC, so it never reserves beyond funded capacity.
            liabilities += (accDelta * totalPrincipal + ACC_PRECISION - 1) / ACC_PRECISION;
        }
        lastCheckpoint = uint64(block.timestamp); // unfunded time dropped — never backfilled
        emit Checkpointed(due, pay, lastCheckpoint);
    }

    function _settle(UserPosition storage u) internal {
        uint256 accrued = (u.principal * accPerShare) / ACC_PRECISION;
        if (accrued > u.rewardDebt) u.owed += accrued - u.rewardDebt;
    }

    function _snapshot(UserPosition storage u) internal {
        u.rewardDebt = (u.principal * accPerShare + ACC_PRECISION - 1) / ACC_PRECISION; // ceil
    }

    // ---------------------------------------------------------------- user actions
    function deposit() external payable onChain nonReentrant protectsPrincipal {
        if (msg.value == 0) revert ZeroAmount();
        _checkpoint();
        UserPosition storage u = positions[msg.sender];
        _settle(u);
        u.principal += msg.value; totalPrincipal += msg.value;
        _snapshot(u);
        emit Deposited(msg.sender, msg.value);
    }

    /// @notice Returns principal only. Never touches the reward reserve.
    function withdraw(uint256 amount) external onChain nonReentrant protectsPrincipal {
        UserPosition storage u = positions[msg.sender];
        if (amount == 0 || amount > u.principal) revert Insufficient();
        _checkpoint(); _settle(u);
        u.principal -= amount; totalPrincipal -= amount;
        _snapshot(u);
        _send(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    /// @notice Pays accrued rewards from the funded reserve only.
    function claim() external onChain nonReentrant protectsPrincipal returns (uint256 paid) {
        _checkpoint();
        UserPosition storage u = positions[msg.sender];
        _settle(u); _snapshot(u);
        paid = u.owed;
        if (paid == 0) revert NothingToClaim();
        if (paid > liabilities || paid > reserveBalance) revert ReserveShortfall();
        u.owed = 0; u.claimed += paid;
        liabilities -= paid; reserveBalance -= paid; totalClaimed += paid;
        _send(msg.sender, paid);
        emit Claimed(msg.sender, paid);
    }

    /// @notice Anyone may advance the accumulator; it only ever caps at funded capacity.
    function checkpoint() external onChain nonReentrant { _checkpoint(); }

    // ---------------------------------------------------------------- owner actions
    /// @notice Faucet-funded reserve top-up. Checkpoints FIRST so it never pays past unfunded time.
    function topUpReserve() external payable onlyOwner onChain nonReentrant protectsPrincipal {
        if (msg.value == 0) revert ZeroAmount();
        _checkpoint();
        reserveBalance += msg.value; totalFunded += msg.value;
        emit ReserveToppedUp(msg.sender, msg.value);
    }

    function setRate(uint256 newBps) external onlyOwner onChain nonReentrant {
        if (newBps > BPS) revert RateOutOfRange();
        _checkpoint();
        emit RateChanged(aprBps, newBps);
        aprBps = newBps;
    }

    /// @notice Owner-authorized simulated collateral seizure. NOT an autonomous liquidation engine.
    ///         Already-accrued rewards stay with the user.
    function seize(address user, uint256 amount, address to) external onlyOwner onChain nonReentrant protectsPrincipal {
        if (to == address(0)) revert ZeroAddress();
        UserPosition storage u = positions[user];
        if (amount == 0 || amount > u.principal) revert Insufficient();
        _checkpoint(); _settle(u);
        u.principal -= amount; totalPrincipal -= amount;
        _snapshot(u);
        _send(to, amount);
        emit Seized(user, amount, to);
    }

    // ---------------------------------------------------------------- views
    function pending(address user) external view returns (uint256) {
        UserPosition memory u = positions[user];
        uint256 acc = accPerShare;
        uint256 dt = block.timestamp - lastCheckpoint;
        if (dt > 0 && totalPrincipal > 0 && aprBps > 0) {
            uint256 due = (totalPrincipal * aprBps * dt) / (BPS * YEAR);
            uint256 available = reserveBalance - liabilities;
            uint256 pay = due < available ? due : available;
            acc += (pay * ACC_PRECISION) / totalPrincipal;
        }
        uint256 accrued = (u.principal * acc) / ACC_PRECISION;
        return u.owed + (accrued > u.rewardDebt ? accrued - u.rewardDebt : 0);
    }

    /// @notice Unreserved reserve still available to fund future accrual (as of last checkpoint).
    function availableReserve() external view returns (uint256) { return reserveBalance - liabilities; }

    function _send(address to, uint256 amount) private {
        (bool ok, ) = payable(to).call{value: amount}("");
        if (!ok) revert TransferFailed();
    }

    /// @notice Plain transfers are rejected so principal and reserve can never be mixed implicitly.
    receive() external payable { revert DirectTransferRejected(); }
    fallback() external payable { revert DirectTransferRejected(); }
}
