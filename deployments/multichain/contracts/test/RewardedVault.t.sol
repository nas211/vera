// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {RewardedCollateralVault} from "../src/RewardedCollateralVault.sol";
import {RewardedVaultFactory} from "../src/RewardedVaultFactory.sol";
import {NativeRewardPool} from "../src/NativeRewardPool.sol";
import {AgentPermissions} from "../src/AgentPermissions.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {Vm} from "./Vault.t.sol";

contract RewardedVaultTest {
    Vm constant vm=Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant KEY=0xA11CE;
    address alice=address(0xA1); address manager=address(0xB2);
    address payable treasury=payable(address(0x7EA));
    RewardedVaultFactory factory; RewardedCollateralVault vault; NativeRewardPool pool;
    function setUp() public {
        vm.chainId(31337); vm.warp(1000);
        factory=new RewardedVaultFactory(vm.addr(KEY),treasury,manager,500);pool=factory.rewardPool();
        vm.deal(alice,30_000 ether); vm.deal(manager,30_000 ether);
        vm.prank(alice); vault=RewardedCollateralVault(payable(factory.createVault()));
        vm.prank(alice); vault.deposit{value:10_000 ether}();
        vm.prank(manager); vault.topUpReserve{value:1000 ether}();
    }
    function certificate(uint8 action,uint256 a,uint256 b,uint256 c) internal returns(bytes memory,uint64) {
        uint64 deadline=uint64(block.timestamp+600);
        (uint8 v,bytes32 r,bytes32 s)=vm.sign(KEY,MessageHashUtils.toEthSignedMessageHash(vault.certificateHash(action,a,b,c,deadline)));
        return (abi.encodePacked(r,s,v),deadline);
    }
    function borrow() internal {
        vm.prank(alice);vault.requestDemoBorrow(keccak256("L"),400e6);
        (bytes memory sig,uint64 d)=certificate(1,400e6,0,0);
        vm.prank(alice);vault.acknowledgeDemoLoan(d,sig);
    }
    function backing() internal view {
        require(address(vault).balance>=vault.collateral(),"unbacked collateral");
        require(address(pool).balance>=pool.rewardReserve(),"unbacked pool");
        (,uint256 liability)=pool.previewIndex();require(liability<=pool.rewardReserve()*pool.DENOMINATOR(),"unbacked liabilities");
        require(vault.rewardLiability()<=vault.rewardReserve(),"unfunded");
        require(pool.totalRewardsClaimed()+pool.rewardReserve()==pool.totalRewardsFunded(),"reserve accounting");
    }
    function testPredictedOwnerVaultAndNoAdministratorPrincipalAccess() public {
        require(factory.predict(alice)==address(vault)); require(vault.owner()==alice);
        vm.prank(manager);vm.expectRevert();vault.withdraw(1);
        vm.prank(manager);vm.expectRevert();vault.claimRewards();
        vm.prank(alice);vm.expectRevert();factory.createVault(); backing();
    }
    function testRequestAndActiveLockPrincipalButPermitFundedRewardClaim() public {
        vm.warp(1000+365 days);
        vm.prank(alice);vault.requestDemoBorrow(keccak256("L"),400e6);
        require(vault.withdrawable()==0);
        vm.prank(alice);vm.expectRevert();vault.withdraw(1);
        vm.prank(alice);require(vault.claimRewards()==500 ether);
        require(vault.collateral()==10_000 ether); backing();
        (bytes memory sig,uint64 d)=certificate(1,400e6,0,0);
        vm.prank(alice);vault.acknowledgeDemoLoan(d,sig);
        vm.prank(alice);vm.expectRevert();vault.withdraw(1);
    }
    function testOnlyMatchingLedgerCertificateUnlocksAndCannotReplay() public {
        borrow();(bytes memory wrong,uint64 d)=certificate(2,401e6,1,0);
        vm.prank(alice);vm.expectRevert();vault.settleDemoLoan(401e6,0,0,d,wrong);
        (bytes memory sig,)=certificate(2,401e6,0,0);
        vm.prank(alice);vault.settleDemoLoan(401e6,0,0,d,sig);
        vm.prank(alice);vm.expectRevert();vault.settleDemoLoan(401e6,0,0,d,sig);
        vm.prank(alice);vault.withdraw(10_000 ether);backing();
    }
    function testSeizureCheckpointsAndNeverUsesRewardReserve() public {
        borrow();vm.warp(1000+365 days);
        (bytes memory sig,uint64 d)=certificate(2,401e6,1000 ether,0);
        vm.prank(alice);vault.settleDemoLoan(401e6,1000 ether,0,d,sig);
        require(vault.rewardLiability()==500 ether);require(vault.rewardReserve()==1000 ether);
        require(vault.collateral()==9000 ether);require(treasury.balance==1000 ether);
        vm.warp(block.timestamp+365 days);vm.prank(alice);require(vault.claimRewards()==950 ether);backing();
    }
    function testExhaustedReserveTopupNeverBackfillsUnfundedTime() public {
        vm.warp(1000+4*365 days);vm.prank(manager);vault.topUpReserve{value:100 ether}();
        require(vault.rewardLiability()==1000 ether);
        vm.prank(alice);require(vault.claimRewards()==1000 ether);
        vm.prank(alice);vm.expectRevert();vault.claimRewards();
        vm.warp(block.timestamp+365 days);vm.prank(alice);require(vault.claimRewards()==100 ether);backing();
    }
    function testLateDepositsAndWithdrawalsCheckpointOldPrincipal() public {
        vm.warp(1000+365 days);vm.prank(alice);vault.deposit{value:10_000 ether}();
        require(vault.rewardLiability()==500 ether);
        vm.prank(alice);vault.withdraw(15_000 ether);
        vm.warp(block.timestamp+365 days);vm.prank(alice);require(vault.claimRewards()==750 ether);backing();
    }
    function testRateChangeUsesOldRateAndRequiresManager() public {
        vm.warp(1000+365 days);vm.prank(alice);vm.expectRevert();pool.setRewardRate(1000);
        vm.prank(manager);pool.setRewardRate(1000);require(vault.rewardLiability()==500 ether);
        vm.prank(manager);vm.expectRevert();pool.setRewardRate(10001);
        vm.warp(block.timestamp+365 days/2);vm.prank(alice);require(vault.claimRewards()==1000 ether);backing();
    }
    function testRepeatedCheckpointsPreserveFractionalCarry() public {
        vm.prank(alice);vault.withdraw(10_000 ether-1000);
        for(uint256 i=1;i<=100;i++){vm.warp(1000+i*1000);vault.checkpointRewards();}
        (uint256 pending,uint256 carry)=vault.previewRewards();
        require(pending==(uint256(1000)*500*100_000)/(10_000*365 days));
        require(carry==(1000*500*100_000)%(10_000*365 days));backing();
    }
    function testPauseDefaultAndWrongChain() public {
        borrow();vm.warp(1000+365 days);vm.prank(alice);vault.emergencyPause();
        (bytes memory sig,uint64 d)=certificate(2,200e6,10_000 ether,200e6);
        vm.prank(alice);vault.settleDemoLoan(200e6,10_000 ether,200e6,d,sig);
        require(vault.badDebtMicro()==200e6);require(vault.withdrawable()==0);
        vm.prank(alice);require(vault.claimRewards()==500 ether);backing();
        vm.chainId(1);vm.prank(alice);vm.expectRevert();vault.claimRewards();
    }
    function testCancelledUncreditedRequestUnlocksWithoutErasingRewards() public {
        vm.warp(1000+365 days);vm.prank(alice);vault.requestDemoBorrow(keccak256("L"),400e6);
        (bytes memory sig,uint64 d)=certificate(3,0,0,0);
        vm.prank(alice);vault.cancelUnissuedLoan(d,sig);
        vm.prank(alice);vault.withdraw(10_000 ether);
        vm.prank(alice);require(vault.claimRewards()==500 ether);backing();
    }
    function testStricterStopLimitSurvivesRevocationAndReauthorization() public {
        borrow();AgentPermissions.Limits memory p=AgentPermissions.Limits(400e6,100e6,uint64(block.timestamp+86400),500_000,1,7,false);
        vm.prank(alice);vault.authorizeAgent(vm.addr(KEY),p);
        vm.prank(alice);vault.revokeAgent();p.maxLoss=2_000_000;
        vm.prank(alice);vault.authorizeAgent(vm.addr(KEY),p);
        (,,,uint32 loss,,,)=vault.limits();require(loss==500_000);require(vault.stopBudgetMicro()==500_000);
        p.maxLoss=2_000_001;vm.prank(alice);vm.expectRevert();vault.authorizeAgent(vm.addr(KEY),p);
    }
    function testPlainTransfersCannotBecomePrincipalOrReserve() public {
        vm.prank(alice);(bool ok,)=address(vault).call{value:1 ether}("");require(!ok);
        require(vault.collateral()==10_000 ether);require(vault.rewardReserve()==1000 ether);backing();
    }
    function testForcedNativeSurplusDoesNotChangeEntitlementOrPrincipal() public {
        vm.deal(address(this),1 ether);
        new ForcedSurplus{value:1 ether}(payable(address(vault)));
        require(address(vault).balance==10_001 ether);
        require(vault.collateral()==10_000 ether);require(vault.rewardReserve()==1000 ether);
        vm.warp(1000+365 days);vm.prank(alice);require(vault.claimRewards()==500 ether);backing();
    }
    function testExpiredAndOtherVaultCertificatesCannotUnlock() public {
        borrow();(bytes memory sig,uint64 d)=certificate(2,400e6,0,0);
        vm.warp(d+1);vm.prank(alice);vm.expectRevert();vault.settleDemoLoan(400e6,0,0,d,sig);
        RewardedCollateralVault original=vault;
        vm.prank(manager);vault=RewardedCollateralVault(payable(factory.createVault()));
        vm.prank(manager);vault.deposit{value:1 ether}();
        vm.prank(manager);vault.requestDemoBorrow(keccak256("L"),400e6);
        (sig,d)=certificate(2,400e6,0,0);
        vm.prank(alice);vm.expectRevert();original.settleDemoLoan(400e6,0,0,d,sig);
        require(original.withdrawable()==0);
    }
    function testStricterStopBudgetSurvivesNewLoan() public {
        borrow();AgentPermissions.Limits memory p=AgentPermissions.Limits(400e6,100e6,uint64(block.timestamp+86400),500_000,1,7,false);
        vm.prank(alice);vault.authorizeAgent(vm.addr(KEY),p);
        (bytes memory sig,uint64 d)=certificate(2,400e6,0,0);
        vm.prank(alice);vault.settleDemoLoan(400e6,0,0,d,sig);
        vm.prank(alice);vault.requestDemoBorrow(keccak256("next loan"),400e6);
        (sig,d)=certificate(1,400e6,0,0);
        vm.prank(alice);vault.acknowledgeDemoLoan(d,sig);p.maxLoss=2_000_000;
        vm.prank(alice);vault.authorizeAgent(vm.addr(KEY),p);
        (,,,uint32 loss,,,)=vault.limits();require(loss==500_000);
    }
    function testRewardTransferFailureAndReentrancyCannotDrainPrincipal() public {
        RewardReceiver receiver=new RewardReceiver(factory);vm.deal(address(receiver),10 ether);
        receiver.deposit();vm.prank(manager);receiver.vault().topUpReserve{value:1 ether}();
        vm.warp(block.timestamp+365 days);receiver.reject(true);vm.expectRevert();receiver.claim();
        require(receiver.vault().rewardReserve()==1001 ether);require(receiver.vault().totalRewardsClaimed()==0);
        receiver.reject(false);receiver.claim();require(receiver.vault().collateral()==10 ether);
        require(receiver.vault().totalRewardsClaimed()==0.5 ether);
    }
    function testFuzzCheckpointFrequencyDoesNotChangeEntitlement(uint32 raw) public {
        uint256 elapsed=uint256(raw)%365 days+1;
        for(uint256 i=1;i<=8;i++){vm.warp(1000+elapsed*i/8);vault.checkpointRewards();}
        (uint256 pending,)=vault.previewRewards();require(pending==10_000 ether*500*elapsed/(10_000*365 days));backing();
    }
}
contract ForcedSurplus {
    constructor(address payable target) payable { selfdestruct(target); }
}
contract RewardReceiver {
    RewardedCollateralVault public vault;bool private rejects;
    constructor(RewardedVaultFactory f){vault=RewardedCollateralVault(payable(f.createVault()));}
    function deposit() external {vault.deposit{value:10 ether}();}
    function reject(bool value) external {rejects=value;}
    function claim() external {vault.claimRewards();}
    receive() external payable {
        require(!rejects,"reject");
        try vault.claimRewards() {revert("reentered claim");}catch{}
        try vault.withdraw(1) {revert("reentered withdrawal");}catch{}
        try vault.requestDemoBorrow(keccak256("reenter"),1) {revert("reentered borrow");}catch{}
    }
}
