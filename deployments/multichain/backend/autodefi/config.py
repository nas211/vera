import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from decimal import Decimal as D
from vela.lighter.config import PaperConfig

ROOT = Path(__file__).resolve().parents[2]
CHAIN_ID = 6281971
MODEL_VARIABLES=('MODEL','MODEL_KEY','MODEL_ENDPOINT')
def model_env(name,default='',values=None):
    """Only the three explicitly authorized VELA model aliases are accepted."""
    if name not in MODEL_VARIABLES:raise ValueError('Unsupported model setting')
    values=os.environ if values is None else values
    keys=[prefix+name for prefix in ('VELA_','VERA_PROTOCOL_','ADP_')]
    configured=[key for key in keys if key in values]
    if len({values[key] for key in configured})>1:
        raise ValueError('Conflicting model configuration: '+', '.join(configured))
    return values[configured[0]] if configured else default
def env(name,default=''):
    """Dedicated VELLA configuration, with explicit compatibility for this project's ADP names."""
    canonical,legacy='VERA_PROTOCOL_'+name,'ADP_'+name
    if canonical in os.environ and legacy in os.environ and os.environ[canonical]!=os.environ[legacy]:
        raise ValueError(f'Conflicting {canonical} and legacy {legacy} configuration')
    return os.getenv(canonical,os.getenv(legacy,default))
@dataclass
class Config:
    origin: str = 'http://127.0.0.1:8802'
    rpc: str = 'https://rpc.testnet.dogeos.com/'
    factory: str = ''
    client_id: str = ''
    walletconnect_project_id: str = ''
    attestor_key: str = ''
    confirmations: int = 3
    db_path: Path = ROOT / '.runtime/lighter-showcase/protocol.sqlite3'
    network_enabled: bool = False
    multichain: bool = False
    demo_staking: bool = False
    def __post_init__(self):
        u=urlsplit(self.origin)
        if not u.hostname or u.username or u.password or u.query or u.fragment or u.path or (u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('localhost','127.0.0.1'))):
            raise ValueError('HTTPS origin or loopback required')
        self.db_path=Path(self.db_path).resolve()
        if not self.db_path.is_relative_to(ROOT/'.runtime/lighter-showcase'):
            raise ValueError('Independent database directory required')
        if self.confirmations<3: raise ValueError('At least three confirmations required')
    @classmethod
    def from_env(cls):
        if env('EXECUTION','PAPER')!='PAPER' or env('LENDING','DEMO')!='DEMO':
            raise ValueError('Real trading and lending are disabled')
        path=env('DB',str(cls.db_path))
        legacy_root=Path('/root/AutonomousDeFi')
        # Preserve an explicit old path after the repository (and its data) moves.
        if Path(path).is_relative_to(legacy_root/'.runtime/lighter-showcase'):
            path=ROOT/Path(path).relative_to(legacy_root)
        return cls(origin=env('ORIGIN',cls.origin),rpc=env('RPC',cls.rpc),
            factory=env('FACTORY'),client_id=env('MYDOGE_CLIENT_ID'),
            walletconnect_project_id=env('WALLETCONNECT_PROJECT_ID'),attestor_key=env('DEMO_ATTESTOR_KEY'),
            db_path=Path(path),network_enabled=env('NETWORK_ENABLED','0')=='1',multichain=env('MULTICHAIN','0')=='1',demo_staking=env('DEMO_STAKING_ENABLED','0')=='1')
    def paper(self):
        return PaperConfig(db_path=self.db_path,taker_fee=D('.0004'),initial_balance=D(1))
