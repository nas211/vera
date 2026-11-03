// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {VaultFactory} from "../src/VaultFactory.sol";
import {DogeCollateralVault} from "../src/DogeCollateralVault.sol";
import {AgentPermissions} from "../src/AgentPermissions.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

interface Vm {
    function chainId(uint256) external;
    function addr(uint256) external returns(address);
    function deal(address,uint256) external;
    function prank(address) external;
    function expectRevert() external;
    function warp(uint256) external;
    function sign(uint256,bytes32) external returns(uint8,bytes32,bytes32);
}
contract VaultTest {
    Vm constant vm=Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant TEST_SIGNER_KEY=0xA11CE;
    VaultFactory factory;
    DogeCollateralVault vault;
    address alice=address(0xA1);
    address bob=address(0xB1);
    address agent=address(0xA6);
    address payable treasury=payable(address(0x7EA));
    bytes32 constant LOAN=keccak256("demo-loan-1");
    function setUp() public {
        vm.chainId(31337);vm.warp(1000);
        factory=new VaultFactory(vm.addr(TEST_SIGNER_KEY),treasury);
        vm.deal(alice,20000 ether);vm.prank(alice);
        vault=DogeCollateralVault(payable(factory.createVault()));
        vm.prank(alice);vault.deposit{value:10000 ether}();
    }
    function certificate(uint8 action,uint256 a,uint256 b,uint256 c,uint64 deadline) internal returns(bytes memory) {
        (uint8 v,bytes32 r,bytes32 s)=vm.sign(TEST_SIGNER_KEY,MessageHashUtils.toEthSignedMessageHash(vault.certificateHash(action,a,b,c,deadline)));
        return abi.encodePacked(r,s,v);
    }
    function borrow() internal {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);
        bytes memory sig=certificate(1,800e6,0,0,1100);
        vm.prank(alice);vault.acknowledgeDemoLoan(1100,sig);
    }
    function authorize(uint32 loss) internal {
        AgentPermissions.Limits memory p=AgentPermissions.Limits(800e6,200e6,2000,loss,2,7,true);
        vm.prank(alice);vault.authorizeAgent(agent,p);
    }
    function testDeterministicOwnerOnlyCreation() public {
        require(factory.predict(alice)==address(vault));require(factory.vaultOf(alice)==address(vault));require(vault.owner()==alice);
        vm.prank(alice);vm.expectRevert();factory.createVault();
        vm.prank(bob);address second=factory.createVault();require(second!=address(vault));require(factory.vaultOf(bob)==second);
    }
    function testDepositsAndWithdrawal() public {
        require(vault.collateral()==10000 ether);vm.prank(alice);vault.withdraw(10000 ether);
        require(vault.collateral()==0);require(alice.balance==20000 ether);
    }
    function testUnauthorizedWithdrawalAndDeposit() public {
        vm.deal(bob,1 ether);vm.prank(bob);vm.expectRevert();vault.withdraw(1 ether);
        vm.prank(bob);vm.expectRevert();vault.deposit{value:1 ether}();
    }
    function testLoanLocksBeforeCreditAcknowledgement() public {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);require(vault.withdrawable()==0);
        vm.prank(alice);vm.expectRevert();vault.withdraw(1);
    }
    function testBorrowRequiresCollateral() public {
        vm.prank(alice);vault.withdraw(10000 ether);
        vm.prank(alice);vm.expectRevert();vault.requestDemoBorrow(LOAN,1e6);
    }
    function testForgedAndReplayedIssuance() public {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);
        bytes memory wrong=certificate(1,900e6,0,0,1100);
        vm.prank(alice);vm.expectRevert();vault.acknowledgeDemoLoan(1100,wrong);
        bytes memory right=certificate(1,800e6,0,0,1100);
        vm.prank(alice);vault.acknowledgeDemoLoan(1100,right);
        vm.prank(alice);vm.expectRevert();vault.acknowledgeDemoLoan(1100,right);
    }
    function testExpiredCertificate() public {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);
        bytes memory sig=certificate(1,800e6,0,0,1100);vm.warp(1101);
        vm.prank(alice);vm.expectRevert();vault.acknowledgeDemoLoan(1100,sig);
    }
    function testCertificateDomainBinding() public {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);bytes memory sig=certificate(1,800e6,0,0,1100);
        vm.chainId(6281971);vm.prank(alice);vm.expectRevert();vault.acknowledgeDemoLoan(1100,sig);
    }
    function testRejectedRequestRequiresCancellationCertificate() public {
        vm.prank(alice);vault.requestDemoBorrow(LOAN,800e6);
        bytes memory sig=certificate(3,0,0,0,1100);
        vm.prank(bob);vm.expectRevert();vault.cancelUnissuedLoan(1100,sig);
        vm.prank(alice);vault.cancelUnissuedLoan(1100,sig);require(vault.withdrawable()==10000 ether);
    }
    function testAuthorizationAndRevocation() public {
        borrow();authorize(1_000_000);vm.prank(alice);vault.startTrading();require(vault.agentAuthorized());
        vm.prank(alice);vault.revokeAgent();require(!vault.agentAuthorized());require(!vault.trading());
        vm.prank(alice);vm.expectRevert();vault.startTrading();
    }
    function testAgentCannotWithdrawOrStart() public {
        borrow();authorize(2_000_000);
        vm.prank(agent);vm.expectRevert();vault.startTrading();
        vm.prank(agent);vm.expectRevert();vault.withdraw(1);
        vm.prank(alice);vault.startTrading();vm.prank(agent);vault.stopTrading();require(!vault.trading());
    }
    function testExpiredPermissions() public {
        borrow();authorize(2_000_000);vm.prank(alice);vault.startTrading();vm.warp(2001);require(!vault.agentAuthorized());
        vm.prank(agent);vault.stopTrading();
    }
    function testUniversalTwoDollarCeiling() public {
        borrow();vm.expectRevert();authorize(2_000_001);authorize(500_000);
        (,,,uint32 loss,,,)=vault.limits();require(loss==500_000);
    }
    function testEmergencyPauseRevokesEntriesButAllowsSettlement() public {
        borrow();authorize(2_000_000);vm.prank(alice);vault.startTrading();vm.prank(alice);vault.emergencyPause();
        require(!vault.trading());require(vault.paused());
        bytes memory sig=certificate(2,801e6,0,0,1100);vm.prank(alice);vault.settleDemoLoan(801e6,0,0,1100,sig);
        require(vault.withdrawable()==0);vm.prank(alice);vault.unpause();require(vault.withdrawable()==10000 ether);
    }
    function testRepayUnlockWithdrawAndNoReplay() public {
        borrow();bytes memory sig=certificate(2,801e6,0,0,1100);
        vm.prank(alice);vault.settleDemoLoan(801e6,0,0,1100,sig);
        vm.prank(alice);vm.expectRevert();vault.settleDemoLoan(801e6,0,0,1100,sig);
        vm.prank(alice);vault.withdraw(10000 ether);require(vault.collateral()==0);
        vm.prank(alice);vault.deposit{value:1 ether}();vm.prank(alice);vm.expectRevert();vault.requestDemoBorrow(LOAN,1e6);
    }
    function testOwnerConfirmedTestnetLossSettlement() public {
        borrow();bytes memory sig=certificate(2,801e6,100 ether,0,1100);
        vm.prank(agent);vm.expectRevert();vault.settleDemoLoan(801e6,100 ether,0,1100,sig);
        vm.prank(alice);vault.settleDemoLoan(801e6,100 ether,0,1100,sig);
        require(vault.collateral()==9900 ether);require(treasury.balance==100 ether);
    }
    function testBadDebtPersistsAndBlocksNewLoans() public {
        borrow();bytes memory sig=certificate(2,600e6,10000 ether,200e6,1100);
        vm.prank(alice);vault.settleDemoLoan(600e6,10000 ether,200e6,1100,sig);
        require(vault.badDebtMicro()==200e6);require(vault.withdrawable()==0);
        require(vault.loanState()==DogeCollateralVault.LoanState.Defaulted);
        vm.prank(alice);vm.expectRevert();vault.requestDemoBorrow(keccak256("second"),1e6);
    }
    function testCannotSeizeMoreThanCollateralOrErasePrincipal() public {
        borrow();bytes memory sig=certificate(2,1,10001 ether,0,1100);
        vm.prank(alice);vm.expectRevert();vault.settleDemoLoan(1,10001 ether,0,1100,sig);
    }
    function testImmutableOwnership() public {
        vm.prank(alice);vm.expectRevert();vault.transferOwnership(bob);
        vm.prank(alice);vm.expectRevert();vault.renounceOwnership();
    }
    function testFuzzWithdrawConservesCollateral(uint96 raw) public {
        uint256 amount=uint256(raw)%10000 ether+1;
        if(amount>10000 ether)amount=10000 ether;
        vm.prank(alice);vault.withdraw(amount);
        require(vault.collateral()+amount==10000 ether);
    }
    function testReentrancyCannotWithdrawTwice() public {
        ReentrantOwner attacker=new ReentrantOwner(factory);vm.deal(address(attacker),2 ether);
        attacker.run();require(address(attacker).balance==2 ether);require(attacker.target().collateral()==0);
    }
}
contract ReentrantOwner {
    DogeCollateralVault public target;
    constructor(VaultFactory f) {target=DogeCollateralVault(payable(f.createVault()));}
    function run() external {target.deposit{value:2 ether}();target.withdraw(2 ether);}
    receive() external payable {try target.withdraw(2 ether) {} catch {}}
}
