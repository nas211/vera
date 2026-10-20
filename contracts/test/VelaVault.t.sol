// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {VelaVault} from "../src/VelaVault.sol";
import {WDOGE} from "../src/WDOGE.sol";
import {TUSDC} from "../src/TUSDC.sol";

interface Vm {
    function deal(address, uint256) external;
    function prank(address) external;
    function warp(uint256) external;
    function expectRevert() external;
    function expectEmit(bool, bool, bool, bool) external;
    function chainId(uint256) external;
}

contract RejectDOGE {
    function start(VelaVault v) external payable { v.startTrading{value: msg.value}(1); }
    function stop(VelaVault v) external { v.stopTrading(); }
}

contract ReenterDOGE {
    VelaVault public vault;
    bool public attempted;
    bool public succeeded;
    constructor(VelaVault v) { vault = v; }
    function start() external payable { vault.startTrading{value: msg.value}(1); }
    function stop() external { vault.stopTrading(); }
    receive() external payable {
        attempted = true;
        (succeeded,) = address(vault).call{value: 1 ether}(abi.encodeCall(vault.startTrading, (1)));
    }
}

contract VelaVaultTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    VelaVault v;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);
    address operator = address(0x1234);
    event TradingStarted(address indexed user, uint256 strategyId, uint256 dogeIn, uint256 usdcCredited, uint256 price);
    event StopRequested(address indexed user);
    event Settled(address indexed user, int256 pnl, uint256 dogeOut, uint256 price);
    event TradeLogged(address indexed user, bytes32 indexed tradeId, string symbol, uint8 side, uint256 size, uint256 entryPrice, uint256 exitPrice, int256 pnl);

    receive() external payable {}
    function setUp() public {
        vm.chainId(31337);
        v = new VelaVault(address(this), operator);
        vm.deal(address(this), 10000 ether);
        vm.deal(alice, 1000 ether);
        vm.deal(bob, 1000 ether);
        price(20_000_000); // $0.20 per test DOGE
        v.fundPool{value: 100 ether}();
    }
    function eq(uint256 a, uint256 b) internal pure { require(a == b, "not equal"); }
    function price(uint256 p) internal { vm.prank(operator); v.updatePrice(p); }
    function start(address u) internal { vm.prank(u); v.startTrading{value: 10 ether}(1); }
    function stop(address u) internal { vm.prank(u); v.stopTrading(); }
    function settle(address u, int256 pnl) internal { vm.prank(operator); v.settle(u, pnl, 20_000_000); }
    function log(address u, bytes32 id, int256 pnl) internal {
        vm.prank(operator); v.logTrade(u, id, "DOGE/USD", 0, 1e18, 20_000_000, 21_000_000, pnl);
    }
    function testStartWrapsMintsAndEmits() public {
        vm.expectEmit(true,false,false,true);
        emit TradingStarted(alice,1,10 ether,2_000_000,20_000_000);
        start(alice);
        eq(v.wdoge().balanceOf(address(v)),110 ether);
        eq(v.tusdc().balanceOf(address(v)),2_000_000);
        eq(v.tusdc().decimals(),6);
        (VelaVault.State state,uint256 strategy,uint256 doge,uint256 usdc,,uint256 nonce)=v.sessions(alice);
        require(state == VelaVault.State.Active);
        eq(strategy,1);eq(doge,10 ether);eq(usdc,2_000_000);eq(nonce,1);
    }
    function testStartRejectsDoubleZeroDustUnknown() public {
        start(alice);
        vm.expectRevert();start(alice);
        vm.expectRevert();vm.prank(bob);v.startTrading(1);
        vm.expectRevert();vm.prank(bob);v.startTrading{value:1}(1);
        vm.expectRevert();vm.prank(bob);v.startTrading{value:1 ether}(999);
    }
    function testPriceOnlyOperatorAndBounds() public {
        vm.expectRevert();v.updatePrice(1);
        vm.expectRevert();price(0);
        vm.expectRevert();price(1e18+1);
        price(30_000_000);eq(v.dogeUsd(),30_000_000);eq(v.priceUpdatedAt(),block.timestamp);
    }
    function testStaleStartAndSettle() public {
        start(alice);stop(alice);
        vm.warp(block.timestamp+121);
        vm.expectRevert();start(bob);
        vm.expectRevert();settle(alice,0);
        price(20_000_000);settle(alice,0);
    }
    function testPriceAgeBoundary() public {
        vm.warp(block.timestamp+120);start(alice);
    }
    function testPauseStopsNewEntriesButAllowsExits() public {
        start(alice);v.pause();
        vm.expectRevert();start(bob);
        stop(alice);log(alice,bytes32(uint256(1)),-100_000);settle(alice,-100_000);
        v.unpause();start(bob);
    }
    function testOwnerOnlyConfiguration() public {
        vm.expectRevert();vm.prank(alice);v.pause();
        vm.expectRevert();vm.prank(alice);v.unpause();
        vm.expectRevert();vm.prank(alice);v.setOperator(bob);
        vm.expectRevert();v.setOperator(address(0));
        vm.expectRevert();vm.prank(alice);v.setStrategy(2,false);
        v.setStrategy(1,false);vm.expectRevert();start(alice);
        v.setStrategy(1,true);v.setOperator(bob);vm.prank(bob);v.updatePrice(1e8);
    }
    function testStopStateAndEvent() public {
        vm.expectRevert();stop(alice);start(alice);
        vm.expectEmit(true,false,false,true);emit StopRequested(alice);stop(alice);
        vm.expectRevert();stop(alice);
    }
    function testTradePermissionsValidationAndDuplicate() public {
        vm.expectRevert();log(alice,bytes32(uint256(1)),1);
        start(alice);
        vm.expectRevert();v.logTrade(alice,bytes32(uint256(1)),"DOGE/USD",0,1,1,1,1);
        vm.expectRevert();vm.prank(operator);v.logTrade(alice,bytes32(0),"DOGE/USD",0,1,1,1,1);
        vm.expectRevert();vm.prank(operator);v.logTrade(alice,bytes32(uint256(1)),"DOGE/USD",2,1,1,1,1);
        vm.expectRevert();vm.prank(operator);v.logTrade(alice,bytes32(uint256(1)),"",0,1,1,1,1);
        vm.expectRevert();vm.prank(operator);v.logTrade(alice,bytes32(uint256(1)),"DOGE/USD",0,0,1,1,1);
        vm.expectEmit(true,true,false,true);
        emit TradeLogged(alice,bytes32(uint256(1)),"DOGE/USD",0,1e18,20_000_000,21_000_000,100_000);
        log(alice,bytes32(uint256(1)),100_000);
        vm.expectRevert();log(alice,bytes32(uint256(1)),100_000);
    }
    function testSettleProfitLossAndRestart() public {
        start(alice);log(alice,bytes32(uint256(1)),200_000);stop(alice);
        vm.expectEmit(true,false,false,true);emit Settled(alice,200_000,11 ether,20_000_000);
        settle(alice,200_000);eq(alice.balance,1001 ether);eq(v.tusdc().totalSupply(),0);
        start(alice);log(alice,bytes32(uint256(2)),-400_000);stop(alice);settle(alice,-400_000);
        eq(alice.balance,999 ether);eq(v.activeUsers(),0);
        (,,,,,uint256 nonce)=v.sessions(alice);eq(nonce,2);
        vm.expectRevert();settle(alice,-400_000);
    }
    function testSettleRejectsNonOperatorActiveMismatchedPriceAndUnloggedPnl() public {
        start(alice);vm.expectRevert();settle(alice,0);stop(alice);
        vm.expectRevert();v.settle(alice,0,20_000_000);
        vm.expectRevert();vm.prank(operator);v.settle(alice,0,19_000_000);
        vm.expectRevert();settle(alice,1);
    }
    function testInsufficientPoolCapsAndReservesOtherUsers() public {
        start(alice);start(bob);log(alice,bytes32(uint256(1)),1000_000_000);stop(alice);
        settle(alice,1000_000_000);
        eq(alice.balance,1100 ether);
        eq(v.wdoge().balanceOf(address(v)),10 ether);eq(v.totalPrincipal(),10 ether);
        stop(bob);settle(bob,0);eq(bob.balance,1000 ether);
    }
    function testTotalLossPaysZero() public {
        start(alice);log(alice,bytes32(uint256(1)),-3_000_000);stop(alice);settle(alice,-3_000_000);
        eq(alice.balance,990 ether);eq(v.tusdc().totalSupply(),0);
    }
    function testPoolFundingAndDirectSend() public {
        uint256 before=v.wdoge().balanceOf(address(v));v.fundPool{value:1 ether}();
        eq(v.wdoge().balanceOf(address(v)),before+1 ether);
        vm.expectRevert();v.fundPool();
        (bool ok,)=address(v).call{value:1 ether}("");require(!ok);
    }
    function testWrapperDepositWithdrawAndTransfer() public {
        WDOGE w=v.wdoge();w.deposit{value:2 ether}();eq(w.balanceOf(address(this)),2 ether);
        w.transfer(alice,1 ether);vm.prank(alice);w.withdraw(1 ether);eq(alice.balance,1001 ether);
        vm.expectRevert();w.withdraw(2 ether);
        vm.expectRevert();w.deposit();
        vm.expectRevert();w.withdraw(0);
        (bool ok,)=address(w).call{value:1 ether}("");require(ok);eq(w.balanceOf(address(this)),2 ether);
    }
    function testTokenMintBurnVaultOnly() public {
        TUSDC token=v.tusdc();
        vm.expectRevert();token.mint(alice,1);
        vm.expectRevert();token.burn(address(v),1);
    }
    function testRejectedNativeTransferKeepsStoppingState() public {
        RejectDOGE r=new RejectDOGE();r.start{value:10 ether}(v);r.stop(v);
        vm.expectRevert();settle(address(r),0);
        (VelaVault.State state,,,,,)=v.sessions(address(r));require(state==VelaVault.State.Stopping);
        eq(v.tusdc().totalSupply(),2_000_000);
    }
    function testReentrancyCannotStartDuringPayout() public {
        ReenterDOGE r=new ReenterDOGE(v);r.start{value:10 ether}();r.stop();settle(address(r),0);
        require(r.attempted()&&!r.succeeded());eq(v.activeUsers(),0);
    }
    function testRejectsMainnetDeployment() public {
        vm.chainId(1);vm.expectRevert();new VelaVault(address(this),operator);
    }
    function testFuzzRoundTripNeverCreatesDOGE(uint96 amount) public {
        uint256 a=uint256(amount)%1000 ether+1e14;
        vm.deal(alice,a);vm.prank(alice);v.startTrading{value:a}(1);stop(alice);settle(alice,0);
        require(alice.balance<=a && a-alice.balance<5e12);
    }
}
