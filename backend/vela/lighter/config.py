import os
from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path
from ..settings import ROOT


@dataclass
class PaperConfig:
    db_path: Path = ROOT / ".runtime/lighter-showcase/paper.sqlite3"
    mode: str = "PAPER"
    initial_balance: D = D("10000")
    max_leverage: D = D(3)  # Preserve Vela's stricter existing ceiling.
    loss_budget: D = D(2)
    maker_fee: D = D(0)
    taker_fee: D = D(0)
    platform_fee: D = D(0)
    slippage: D = D(".0002")
    spread: D = D(0)  # Additional half spread; actual book spread is always included.
    latency_ms: int = 300

    def __post_init__(self):
        self.db_path = Path(self.db_path).resolve()
        if not self.db_path.is_relative_to(ROOT / ".runtime/lighter-showcase"):
            raise ValueError("Showcase requires its own isolated database directory")
        if self.mode != "PAPER":
            raise ValueError("LIVE is disabled; paper execution only")
        for name in ("initial_balance","max_leverage","loss_budget","maker_fee","taker_fee","platform_fee","slippage","spread"):
            value = D(str(getattr(self, name)))
            if not value.is_finite():
                raise ValueError("Nonfinite simulation setting")
            setattr(self, name, value)
        if not 0 < self.initial_balance <= 1_000_000 or not 1 <= self.max_leverage <= 3 or self.loss_budget <= 0:
            raise ValueError("Invalid simulation risk configuration")
        self.loss_budget = min(D(2), self.loss_budget)
        if any(not 0 <= x < D(".005") for x in (self.maker_fee,self.taker_fee,self.platform_fee,self.slippage,self.spread)):
            raise ValueError("Invalid simulation costs")
        if self.maker_fee+self.platform_fee >= D(".01") or self.taker_fee+self.platform_fee >= D(".01"):
            raise ValueError("Combined fees too high")
        if not 0 <= self.latency_ms <= 5000:
            raise ValueError("Invalid latency")

    @classmethod
    def from_env(cls):
        return cls(db_path=Path(os.getenv("VELA_PAPER_DB",str(cls.db_path))),
                   mode=os.getenv("VELA_EXECUTION_MODE","PAPER"),
                   loss_budget=D(os.getenv("VELA_LOSS_BUDGET","2")),
                   maker_fee=D(os.getenv("VELA_PAPER_MAKER_FEE","0")),
                   taker_fee=D(os.getenv("VELA_PAPER_TAKER_FEE","0")),
                   platform_fee=D(os.getenv("VELA_PAPER_PLATFORM_FEE","0")),
                   slippage=D(os.getenv("VELA_PAPER_SLIPPAGE",".0002")),
                   spread=D(os.getenv("VELA_PAPER_SPREAD","0")),
                   latency_ms=int(os.getenv("VELA_PAPER_LATENCY_MS","300")))

    def public(self):
        return dict(mode="PAPER",liveEnabled=False,initialBalance=str(self.initial_balance),
                    maxLeverage=str(self.max_leverage),maxLossBudget=str(min(D(2),self.loss_budget)),
                    makerFee=str(self.maker_fee),takerFee=str(self.taker_fee),platformFee=str(self.platform_fee),
                    slippage=str(self.slippage),additionalHalfSpread=str(self.spread),latencyMs=self.latency_ms,
                    funding="Prorated hourly funding estimate during observed fresh data; unobserved intervals flagged",
                    stopBudgetIsFillGuarantee=False)
