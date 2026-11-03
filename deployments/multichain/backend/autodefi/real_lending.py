"""Future genuine liquidity interface. No implementation is enabled."""
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class LiquidityState:
    cash_micro:int
    principal_receivable_micro:int
    accrued_interest_micro:int
    reserves_micro:int
    bad_debt_micro:int
    lender_shares:int

class LendingMarket(Protocol):
    def liquidity(self)->LiquidityState: ...
    def deposit_liquidity(self,owner:str,amount_micro:int): ...
    def withdraw_liquidity(self,owner:str,shares:int): ...
    def borrow(self,vault:str,amount_micro:int): ...
    def repay(self,vault:str,amount_micro:int): ...
    def liquidate(self,vault:str): ...

class RealLendingDisabled:
    def __init__(self,*args,**kwargs):
        raise RuntimeError('Real lending disabled: verified liquidity, oracle, settlement and independent security review required')
