"""Paper maker-only overnight profile; supports preserving an existing instance."""
import runner, overnight, v2
from v2 import PaperV2

class MakerOnlyPaper(PaperV2):
    def snapshot(self):
        d=super().snapshot()
        d['strategy']='Maker entries / maker exits — paper'
        d['pending_market_exits']=0
        return d

def configure():
    overnight.configure()
    # User-requested entry pause. Keep TAO subscribed so existing lots can exit.
    v2.EXCLUDED_ENTRIES.add("TAOUSDT-PERP")
    v2.ACTIVE_MARKETS[:]=[m for m in runner.MARKETS if m not in v2.EXCLUDED_ENTRIES]
    runner.CONFIG.update(strategy_version=5,exit_mode='maker_post_only',taker_orders=False)

if __name__=='__main__':
    configure()
    runner.main(MakerOnlyPaper)
