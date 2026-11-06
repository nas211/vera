// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {TestDogeLending} from "../src/TestDogeLending.sol";
import {TestDogeRepaymentRouter,IDogeConversion} from "../src/TestDogeRepaymentRouter.sol";
import {Vm} from "./Vault.t.sol";
contract FixtureUSDC is ERC20 {
    constructor()ERC20("Local test dollars","LOCAL-USDC"){_mint(msg.sender,1000000e6);}
    function decimals()public pure override returns(uint8){return 6;}
}
contract FixtureConversion is IDogeConversion {
    FixtureUSDC token;uint256 public output=1 ether;bool public consume=true;
    constructor(FixtureUSDC t){token=t;}
    receive()external payable{}
    function setOutput(uint256 n)external{output=n;}
    function setConsume(bool c)external{consume=c;}
    function swapUsdcForDoge(uint256 amount,uint256,uint256)external{
        if(consume)token.transferFrom(msg.sender,address(this),amount);
        (bool ok,)=payable(msg.sender).call{value:output}("");require(ok);
    }
}
contract TestDogeRepaymentRouterTest {
    Vm constant vm=Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address alice=address(0xA1);TestDogeLending market;FixtureUSDC token;FixtureConversion venue;TestDogeRepaymentRouter router;
    receive()external payable{}
    function setUp()public{
        vm.chainId(31337);vm.warp(1000);vm.deal(address(this),100 ether);vm.deal(alice,20 ether);
        market=new TestDogeLending(address(this));market.fund{value:20 ether}();
        vm.prank(alice);market.deposit{value:10 ether}(0,1600);
        vm.prank(alice);market.borrow(7 ether,1,1600);
        token=new FixtureUSDC();venue=new FixtureConversion(token);vm.deal(address(venue),20 ether);
        router=new TestDogeRepaymentRouter(token,venue,market);token.transfer(alice,20e6);
        vm.prank(alice);token.approve(address(router),20e6);
    }
    function testUsdcProducesActualDogeAndReducesDebt()public{
        vm.prank(alice);uint256 paid=router.repayFromUsdc(1e6,1 ether,1200);
        require(paid==1 ether&&market.debt(alice)==6 ether);require(token.balanceOf(alice)==19e6);
        require(market.liquidity()==14 ether);require(token.allowance(address(router),address(venue))==0);
    }
    function testOutputBelowMinimumRollsBackEveryBalance()public{
        venue.setOutput(.9 ether);vm.prank(alice);vm.expectRevert();router.repayFromUsdc(1e6,1 ether,1200);
        require(market.debt(alice)==7 ether);require(token.balanceOf(alice)==20e6);require(address(venue).balance==20 ether);
    }
    function testConversionFailureCannotReduceDebt()public{
        vm.deal(address(venue),0);vm.prank(alice);vm.expectRevert();router.repayFromUsdc(1e6,1 ether,1200);
        require(market.debt(alice)==7 ether);require(token.balanceOf(alice)==20e6);
    }
    function testExcessDogeReturnedToBorrower()public{
        venue.setOutput(8 ether);uint256 beforeBalance=alice.balance;
        vm.prank(alice);router.repayFromUsdc(8e6,7 ether,1200);
        require(market.debt(alice)==0);require(alice.balance==beforeBalance+1 ether);require(address(router).balance==0);
    }
    function testExpiredQuoteAndUnspentUsdcFailClosed()public{
        vm.prank(alice);vm.expectRevert();router.repayFromUsdc(1e6,1 ether,999);
        venue.setConsume(false);vm.prank(alice);vm.expectRevert();router.repayFromUsdc(1e6,1 ether,1200);
        require(token.balanceOf(alice)==20e6);require(market.debt(alice)==7 ether);
    }
    function testPaperNumberCannotRepayWithoutTokenAllowance()public{
        vm.prank(alice);token.approve(address(router),0);
        vm.prank(alice);vm.expectRevert();router.repayFromUsdc(1e6,1 ether,1200);
        require(market.debt(alice)==7 ether);
    }
}
