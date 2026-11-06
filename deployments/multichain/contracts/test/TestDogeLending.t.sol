// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {TestDogeLending} from "../src/TestDogeLending.sol";
import {Vm} from "./Vault.t.sol";

interface VmLending is Vm { function startPrank(address) external; function stopPrank() external; }
contract TestDogeLendingTest {
    VmLending constant vm = VmLending(address(uint160(uint256(keccak256("hevm cheat code")))));
    address alice = address(0xA1);
    address bob = address(0xB1);
    TestDogeLending market;
    receive() external payable {}
    function setUp() public {
        vm.chainId(31337); vm.warp(1000);
        market = new TestDogeLending(address(this));
        vm.deal(address(this),1000 ether); vm.deal(alice,100 ether); vm.deal(bob,100 ether);
        market.fund{value:100 ether}();
        vm.startPrank(alice); market.deposit{value:10 ether}(0,1600);vm.stopPrank();
    }
    function revision() internal view returns(uint256) { return market.position(alice).revision; }
    function borrow(uint256 n) internal { vm.startPrank(alice); market.borrow(n,revision(),block.timestamp+600);vm.stopPrank(); }
    function backing() internal view { require(address(market).balance >= market.liquidity()+market.totalCollateral()+market.totalAvailable(),"backing"); }
    function testTenDogeBorrowsSevenWithWithdrawableBackedProceeds() public {
        borrow(7 ether); require(market.debt(alice)==7 ether); require(market.capacity(alice)==0);
        require(market.position(alice).available==7 ether); require(alice.balance==90 ether);
        vm.startPrank(alice); market.withdrawProceeds(7 ether,revision(),1600);vm.stopPrank();
        require(alice.balance==97 ether); require(market.debt(alice)==7 ether);
        require(market.position(alice).available==0); backing();
    }
    function testPoolCannotLendCollateralOrAlreadyReservedProceeds() public {
        market.withdrawLiquidity(99 ether); require(market.capacity(alice)==1 ether);
        vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.borrow(2 ether,reviewedRevision,1600);}vm.stopPrank();
        borrow(1 ether); vm.expectRevert();market.withdrawLiquidity(1);
        require(market.position(alice).collateral==10 ether); backing();
    }
    function testNoBorrowAboveSeventyPercent() public {
        vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.borrow(7 ether+1,reviewedRevision,1600);}vm.stopPrank();
        borrow(7 ether); vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.borrow(1,reviewedRevision,1600);}vm.stopPrank(); backing();
    }
    function testWalletRepayReturnsExcessAndUnlocksCollateral() public {
        borrow(7 ether); vm.startPrank(alice);market.withdrawProceeds(7 ether,revision(),1600);vm.stopPrank();
        vm.startPrank(alice);uint256 paid=market.repay{value:8 ether}(revision(),1600);vm.stopPrank();
        require(paid==7 ether);require(alice.balance==90 ether);require(market.debt(alice)==0);
        vm.startPrank(alice);market.withdrawCollateral(10 ether,revision(),1600);vm.stopPrank();
        require(alice.balance==100 ether);backing();
    }
    function testInterestPaidBeforePrincipalAndOneYearSimpleRate() public {
        borrow(7 ether);vm.warp(1000+365 days);
        require(market.debt(alice)==7.35 ether);
        vm.startPrank(alice);market.repayAvailable(.2 ether,revision(),block.timestamp+600);vm.stopPrank();
        require(market.position(alice).principal==7 ether);require(market.position(alice).interest==.15 ether);
        vm.startPrank(alice);market.repayAvailable(.3 ether,revision(),block.timestamp+600);vm.stopPrank();
        require(market.position(alice).principal==6.85 ether);backing();
    }
    function testFractionalInterestSurvivesRepeatedCheckpoints() public {
        borrow(1000);for(uint256 i=1;i<=100;i++){vm.warp(1000+i*1000);vm.startPrank(alice);market.deposit{value:1}(revision(),block.timestamp+600);vm.stopPrank();}
        require(market.position(alice).interest==(uint256(1000)*500*100000)/(10000*365 days));
        require(market.position(alice).interestRemainder==(uint256(1000)*500*100000)%(10000*365 days));backing();
    }
    function testReplayAndExpiredRequestRevert() public {
        uint256 rev=revision();borrow(1 ether);
        vm.startPrank(alice);vm.expectRevert();market.borrow(1 ether,rev,1600);vm.stopPrank();
        vm.warp(1700);vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.borrow(1 ether,reviewedRevision,1600);}vm.stopPrank();backing();
    }
    function testCollateralLockedUntilDebtZero() public {
        borrow(1 ether);vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.withdrawCollateral(1,reviewedRevision,1600);}vm.stopPrank();
        vm.startPrank(alice);market.repayAvailable(1 ether,revision(),1600);vm.stopPrank();
        vm.startPrank(alice);market.withdrawCollateral(10 ether,revision(),1600);vm.stopPrank();backing();
    }
    function testPauseKeepsRepayAndWithdrawAvailable() public {
        borrow(1 ether);market.setBorrowingPaused(true);
        require(market.capacity(alice)==0);vm.startPrank(alice);{uint256 reviewedRevision=revision();vm.expectRevert();market.borrow(1,reviewedRevision,1600);}vm.stopPrank();
        vm.startPrank(alice);market.withdrawProceeds(1 ether,revision(),1600);vm.stopPrank();
        vm.startPrank(alice);market.repay{value:1 ether}(revision(),1600);vm.stopPrank();
        vm.startPrank(alice);market.withdrawCollateral(10 ether,revision(),1600);vm.stopPrank();backing();
    }
    function testOtherUserCannotAccessFundsOrAdministration() public {
        borrow(7 ether);
        vm.prank(bob);vm.expectRevert();market.withdrawProceeds(1 ether,0,1600);
        vm.prank(bob);vm.expectRevert();market.withdrawCollateral(1 ether,0,1600);
        vm.prank(bob);vm.expectRevert();market.withdrawLiquidity(1 ether);
        vm.prank(bob);vm.expectRevert();market.setBorrowingPaused(true);backing();
    }
    function testRepayForNeedsDeliveredNativeDogeAndCannotWithdraw() public {
        borrow(7 ether);vm.prank(bob);vm.expectRevert();market.repayFor(alice);
        vm.prank(bob);market.repayFor{value:1 ether}(alice);require(market.debt(alice)==6 ether);
        require(market.position(alice).available==7 ether);backing();
    }
    function testCollateralRepaymentIsExplicitAndConserved() public {
        borrow(7 ether);vm.startPrank(alice);market.withdrawProceeds(7 ether,revision(),1600);vm.stopPrank();
        vm.startPrank(alice);market.repayCollateral(7 ether,revision(),1600);vm.stopPrank();
        require(market.debt(alice)==0);require(market.position(alice).collateral==3 ether);
        require(market.liquidity()==100 ether);backing();
    }
    function testDirectTransfersAndWrongNetworkRejected() public {
        (bool ok,)=address(market).call{value:1}("");require(!ok);
        vm.chainId(1);vm.expectRevert();market.fund{value:1}();vm.expectRevert();new TestDogeLending(address(this));
    }
    function testFuzzBorrowWithdrawRepayConservation(uint96 input) public {
        uint256 n=uint256(input)%(7 ether)+1;borrow(n);
        vm.startPrank(alice);market.withdrawProceeds(n,revision(),1600);vm.stopPrank();backing();
        vm.startPrank(alice);market.repay{value:n}(revision(),1600);vm.stopPrank();backing();
        require(market.debt(alice)==0);require(market.liquidity()==100 ether);
    }
}
