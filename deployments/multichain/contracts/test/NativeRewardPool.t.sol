// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {NativeRewardPool} from "../src/NativeRewardPool.sol";
import {RewardedVaultFactory} from "../src/RewardedVaultFactory.sol";
import {RewardedCollateralVault} from "../src/RewardedCollateralVault.sol";
import {Vm} from "./Vault.t.sol";

contract NativeRewardPoolTest {
    Vm constant vm=Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address alice=address(0xA1);address bob=address(0xB1);address manager=address(0xC1);
    RewardedVaultFactory factory;NativeRewardPool pool;RewardedCollateralVault a;RewardedCollateralVault b;
    function setUp() public {
        vm.chainId(31337);vm.warp(1000);
        factory=new RewardedVaultFactory(address(0x51),payable(address(0x71)),manager,500);pool=factory.rewardPool();
        vm.deal(alice,10_000 ether);vm.deal(bob,10_000 ether);vm.deal(manager,10_000 ether);
        vm.prank(alice);a=RewardedCollateralVault(payable(factory.createVault()));
        vm.prank(bob);b=RewardedCollateralVault(payable(factory.createVault()));
        vm.prank(alice);a.deposit{value:1000 ether}();vm.prank(bob);b.deposit{value:2000 ether}();
    }
    function fund(uint256 amount) internal {vm.prank(manager);pool.topUpReserve{value:amount}();}
    function backing() internal view {
        (,uint256 liability)=pool.previewIndex();require(liability<=pool.rewardReserve()*pool.DENOMINATOR());
        require(address(pool).balance>=pool.rewardReserve());
        require(address(a).balance==a.collateral()&&address(b).balance==b.collateral());
        require(pool.rewardReserve()+pool.totalRewardsClaimed()==pool.totalRewardsFunded());
    }
    function testOneProjectDonationFundsBothOwnersAndPaysThemDirectly() public {
        fund(150 ether);require(pool.totalPrincipal()==3000 ether);require(address(a).balance==1000 ether);
        vm.warp(1000+365 days);uint256 before=alice.balance;
        vm.prank(alice);require(a.claimRewards()==50 ether);require(alice.balance==before+50 ether);
        vm.prank(bob);require(b.claimRewards()==100 ether);require(pool.rewardReserve()==0);backing();
    }
    function testScarceReserveAllocatesAcrossOwnersBeforeEitherClaims() public {
        fund(75 ether);vm.warp(1000+365 days);
        (uint256 aliceDue,)=a.previewRewards();(uint256 bobDue,)=b.previewRewards();
        require(aliceDue==25 ether&&bobDue==50 ether);
        vm.prank(bob);require(b.claimRewards()==bobDue);vm.prank(alice);require(a.claimRewards()==aliceDue);backing();
    }
    function testLaterDepositCannotTakeEarlierPoolRewards() public {
        fund(150 ether);vm.warp(1000+365 days/2);
        vm.prank(bob);b.withdraw(2000 ether);
        vm.warp(1000+365 days);
        vm.prank(alice);require(a.claimRewards()==50 ether);vm.prank(bob);require(b.claimRewards()==50 ether);backing();
    }
    function testNewVaultAfterRateChangeUsesSharedPolicyAndDeterministicAddress() public {
        fund(1000 ether);vm.prank(manager);pool.setRewardRate(1000);
        address owner=address(0xD1);address predicted=factory.predict(owner);
        vm.prank(owner);RewardedCollateralVault v=RewardedCollateralVault(payable(factory.createVault()));
        require(address(v)==predicted&&v.aprBps()==1000&&address(v.rewardPool())==address(pool));backing();
    }
    function testUnfundedTimeIsNeverBackfilledForAnyOwner() public {
        vm.warp(1000+365 days);fund(150 ether);
        (uint256 due,)=a.previewRewards();require(due==0);
        vm.warp(block.timestamp+365 days);vm.prank(alice);require(a.claimRewards()==50 ether);
        vm.prank(bob);require(b.claimRewards()==100 ether);backing();
    }
    function testOnlyFactoryCanRegisterAndOnlyRegisteredVaultCanUpdateOrClaim() public {
        vm.prank(alice);vm.expectRevert();pool.registerVault(alice,alice);
        vm.prank(alice);vm.expectRevert();pool.updatePrincipal(1e30);
        vm.prank(manager);vm.expectRevert();pool.claim();
        vm.prank(bob);vm.expectRevert();a.claimRewards();backing();
    }
    function testPoolHasNoOwnerWithdrawalAndDirectTransfersCannotFundIt() public {
        fund(100 ether);vm.prank(manager);(bool ok,)=address(pool).call(abi.encodeWithSignature("withdraw(uint256)",100 ether));require(!ok);
        vm.prank(alice);(ok,)=address(pool).call{value:1 ether}("");require(!ok);backing();
    }
    function testZeroRateAndChainChangesNeverCreateRewards() public {
        fund(100 ether);vm.prank(manager);pool.setRewardRate(0);vm.warp(1000+365 days);
        (uint256 due,)=a.previewRewards();require(due==0);
        vm.chainId(1);vm.prank(manager);vm.expectRevert();pool.topUpReserve{value:1 ether}();
        vm.prank(manager);vm.expectRevert();pool.setRewardRate(500);
    }
    function testFuzzClaimsNeverExceedSharedFunding(uint64 rawFund,uint32 rawTime) public {
        uint256 amount=uint256(rawFund)+1;fund(amount);vm.warp(1000+uint256(rawTime)% (3*365 days));
        (uint256 x,)=a.previewRewards();(uint256 y,)=b.previewRewards();require(x+y<=amount);
        if(x>0){vm.prank(alice);require(a.claimRewards()==x);}
        if(y>0){vm.prank(bob);require(b.claimRewards()==y);}
        backing();
    }
}
