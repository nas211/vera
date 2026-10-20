// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {VelaVaultV2} from "../src/VelaVaultV2.sol";
import {VelaVault} from "../src/VelaVault.sol";

interface VmV2 {
    function deal(address, uint256) external;
    function prank(address) external;
    function warp(uint256) external;
    function expectRevert() external;
    function chainId(uint256) external;
    function addr(uint256) external returns(address);
    function sign(uint256, bytes32) external returns(uint8, bytes32, bytes32);
}

contract VelaVaultV2Test {
    VmV2 constant vm = VmV2(address(uint160(uint256(keccak256("hevm cheat code")))));
    VelaVaultV2 v;
    uint256 constant OPERATOR_KEY = 123456; // Local test fixture only.
    address operator;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);
    receive() external payable {}
    function setUp() public {
        vm.chainId(31337);vm.warp(1000);
        operator = vm.addr(OPERATOR_KEY);
        v = new VelaVaultV2(address(this),operator);
        vm.deal(address(this),1000 ether);vm.deal(alice,100 ether);vm.deal(bob,100 ether);
        v.fundPool{value:10 ether}();
    }
    function signature(address user,uint256 amount,uint256 nonce) internal returns(bytes memory) {
        bytes32 domain = keccak256(abi.encode(keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256("VelaVault"),keccak256("2"),block.chainid,address(v)));
        bytes32 data = keccak256(abi.encode(v.START_QUOTE_TYPEHASH(),user,1,amount,20_000_000,1000,1120,nonce));
        (uint8 y,bytes32 r,bytes32 s) = vm.sign(OPERATOR_KEY,keccak256(abi.encodePacked("\x19\x01",domain,data)));
        return abi.encodePacked(r,s,y);
    }
    function start() internal {
        bytes memory sig=signature(alice,5 ether,1);
        vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
    }
    function testQuotedStartAndAtomicSettlementWithoutPriceWrites() public {
        start();require(v.dogeUsd()==0);
        vm.prank(alice);v.stopTrading();
        uint256 before=alice.balance;
        vm.prank(operator);v.settleAtPrice(alice,0,20_000_000,1000);
        require(alice.balance==before+5 ether && v.activeUsers()==0);
    }
    function testQuoteBindsWalletValueStrategyPriceAndNonce() public {
        bytes memory sig=signature(alice,5 ether,1);
        vm.expectRevert();vm.prank(bob);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:6 ether}(1,20_000_000,1000,1120,1,sig);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(2,20_000_000,1000,1120,1,sig);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,30_000_000,1000,1120,1,sig);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,2,sig);
        start();vm.prank(alice);v.stopTrading();vm.prank(operator);v.settleAtPrice(alice,0,20_000_000,1000);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
    }
    function testQuoteCannotCrossVaultChainOrRotatedOperator() public {
        bytes memory sig=signature(alice,5 ether,1);
        VelaVaultV2 other=new VelaVaultV2(address(this),operator);
        vm.expectRevert();vm.prank(alice);other.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
        vm.chainId(6281971);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
        vm.chainId(31337);v.setOperator(bob);
        vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
    }
    function testExpiredFutureAndPausedQuotes() public {
        bytes memory sig=signature(alice,5 ether,1);
        vm.warp(999);vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
        vm.warp(1121);vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
        vm.warp(1000);v.pause();vm.expectRevert();vm.prank(alice);v.startTradingWithQuote{value:5 ether}(1,20_000_000,1000,1120,1,sig);
    }
    function testWithdrawOnlyPausedOwnerWithoutActiveOrStoppingUsers() public {
        vm.expectRevert();v.withdrawPool(1 ether,payable(alice));
        start();v.pause();vm.expectRevert();v.withdrawPool(1 ether,payable(alice));
        vm.prank(alice);v.stopTrading();vm.expectRevert();v.withdrawPool(1 ether,payable(alice));
        vm.prank(operator);v.settleAtPrice(alice,0,20_000_000,1000);
        vm.expectRevert();vm.prank(bob);v.withdrawPool(1 ether,payable(bob));
        vm.expectRevert();v.withdrawPool(11 ether,payable(alice));
        vm.expectRevert();v.withdrawPool(0,payable(alice));
        vm.expectRevert();v.withdrawPool(1 ether,payable(address(0)));
        uint256 before=alice.balance;v.withdrawPool(10 ether,payable(alice));
        require(alice.balance==before+10 ether && v.wdoge().balanceOf(address(v))==0);
    }
    function testAtomicSettlementRejectsStaleUnauthorizedAndUnlogged() public {
        start();vm.prank(alice);v.stopTrading();
        vm.expectRevert();v.settleAtPrice(alice,0,20_000_000,1000);
        vm.expectRevert();vm.prank(operator);v.settleAtPrice(alice,1,20_000_000,1000);
        vm.warp(1121);vm.expectRevert();vm.prank(operator);v.settleAtPrice(alice,0,20_000_000,1000);
        vm.expectRevert();vm.prank(operator);v.settleAtPrice(alice,0,20_000_000,1122);
    }
}
