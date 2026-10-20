import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CHAIN_FILE = ROOT / "config/dogeos-testnet.json"


@dataclass
class Settings:
    db_path: Path = ROOT / ".runtime/vela.sqlite3"
    rpc: str = ""
    chain_id: int = 6281971
    confirmations: int = 3
    max_leverage: Decimal = Decimal("3")
    size_fraction: Decimal = Decimal("0.10")
    slippage: Decimal = Decimal("0.0002")
    fee: Decimal = Decimal("0.0005")
    loss_budget: Decimal = Decimal("2")
    max_price_age: int = 60
    chain_writes: bool = False
    max_tx_fee: Decimal = Decimal("0.25")
    daily_fee_budget: Decimal = Decimal("0.5")
    gas_reserve: Decimal = Decimal("1")
    config: dict | None = None

    def __post_init__(self):
        self.config = self.config or json.loads(CHAIN_FILE.read_text())
        self.rpc = self.rpc or self.config["rpcUrls"][0]
        if self.chain_id != 6281971:
            u = urlsplit(self.rpc)
            if self.chain_id != 31337 or u.hostname not in ("127.0.0.1", "localhost"):
                raise ValueError("Only DogeOS testnet or loopback Anvil is allowed")
        self.db_path = Path(self.db_path).resolve()
        if not self.db_path.is_relative_to(ROOT):
            raise ValueError("Vela database must stay inside /root/Vera")
        for value in (self.max_leverage, self.size_fraction, self.slippage, self.fee, self.loss_budget,
                      self.max_tx_fee, self.daily_fee_budget, self.gas_reserve):
            if not value.is_finite():
                raise ValueError("Nonfinite risk setting")
        if not 0 < self.max_leverage <= 3 or not 0 < self.size_fraction <= 1:
            raise ValueError("Invalid sizing/leverage")
        if not 0 <= self.slippage <= Decimal(".01") or not Decimal(".0005") <= self.fee < Decimal(".01"):
            raise ValueError("Invalid costs")
        if self.loss_budget <= 0:
            raise ValueError("Invalid loss budget")
        self.loss_budget = min(Decimal("2"), self.loss_budget)
        if not 0 < self.max_tx_fee <= self.daily_fee_budget or self.gas_reserve < 0:
            raise ValueError("Invalid network fee budget")

    @classmethod
    def from_env(cls):
        return cls(
            db_path=Path(os.getenv("VELA_DB_PATH", str(ROOT / ".runtime/vela.sqlite3"))),
            rpc=os.getenv("VELA_RPC_URL", ""),
            confirmations=int(os.getenv("VELA_CONFIRMATIONS", "3")),
            max_leverage=Decimal(os.getenv("VELA_MAX_LEVERAGE", "3")),
            size_fraction=Decimal(os.getenv("VELA_SIZE_FRACTION", ".10")),
            slippage=Decimal(os.getenv("VELA_SLIPPAGE", ".0002")),
            loss_budget=Decimal(os.getenv("VELA_LOSS_BUDGET", "2")),
            chain_writes=os.getenv("VELA_CHAIN_WRITES", "false").lower() == "true",
            max_tx_fee=Decimal(os.getenv("VELA_MAX_TX_FEE_DOGE", "0.25")),
            daily_fee_budget=Decimal(os.getenv("VELA_DAILY_FEE_BUDGET_DOGE", "0.5")),
            gas_reserve=Decimal(os.getenv("VELA_GAS_RESERVE_DOGE", "1")),
        )
