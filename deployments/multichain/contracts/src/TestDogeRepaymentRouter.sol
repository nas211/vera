// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {TestDogeLending} from "./TestDogeLending.sol";

interface IDogeConversion {
    function swapUsdcForDoge(uint256 amount, uint256 minimumDoge, uint256 deadline) external;
}

/// Only for an independently verified, funded testnet conversion venue.
/// Not deployed/configured by the lending release. Does not read or redeem paper P&L.
contract TestDogeRepaymentRouter is ReentrancyGuard {
    using SafeERC20 for IERC20;
    IERC20 public immutable usdc;
    IDogeConversion public immutable conversion;
    TestDogeLending public immutable market;
    uint256 public immutable deployedChainId;
    error InvalidRoute();
    error ExpiredQuote();
    error InsufficientOutput();
    error TransferFailed();
    event ConvertedAndRepaid(address indexed borrower,uint256 usdcSpent,uint256 dogePaid,uint256 dogeReturned);
    constructor(IERC20 token, IDogeConversion venue, TestDogeLending lending) {
        if ((block.chainid!=6281971&&block.chainid!=31337)||address(token).code.length==0||address(venue).code.length==0||address(lending).code.length==0||lending.deployedChainId()!=block.chainid) revert InvalidRoute();
        usdc=token;conversion=venue;market=lending;deployedChainId=block.chainid;
    }
    receive() external payable {
        if(msg.sender!=address(conversion)&&msg.sender!=address(market))revert InvalidRoute();
    }
    function repayFromUsdc(uint256 amount,uint256 minimumDoge,uint256 deadline) external nonReentrant returns(uint256 paid) {
        if(block.chainid!=deployedChainId)revert InvalidRoute();
        if(deadline<block.timestamp||deadline>block.timestamp+5 minutes)revert ExpiredQuote();
        if(amount==0||minimumDoge==0||market.debt(msg.sender)==0)revert InvalidRoute();
        uint256 nativeBefore=address(this).balance;
        uint256 tokenBefore=usdc.balanceOf(address(this));
        usdc.safeTransferFrom(msg.sender,address(this),amount);
        if(usdc.balanceOf(address(this))!=tokenBefore+amount)revert InvalidRoute();
        usdc.forceApprove(address(conversion),amount);
        conversion.swapUsdcForDoge(amount,minimumDoge,deadline);
        usdc.forceApprove(address(conversion),0);
        if(usdc.balanceOf(address(this))!=tokenBefore)revert InvalidRoute();
        uint256 received=address(this).balance-nativeBefore;
        if(received<minimumDoge)revert InsufficientOutput();
        paid=market.repayFor{value:received}(msg.sender);
        uint256 refund=address(this).balance-nativeBefore;
        if(refund>0){(bool ok,)=payable(msg.sender).call{value:refund}("");if(!ok)revert TransferFailed();}
        emit ConvertedAndRepaid(msg.sender,amount,paid,refund);
    }
}
