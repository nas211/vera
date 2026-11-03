"""Adapters for demo collateral, using the original vault/loan/event/cash tables."""
import json,time,uuid
from decimal import Decimal as D
from vela.db import get,put
from .adapters import adapter,split_owner
from .lending import Lending,number,down,WEI
from .store import event

class SimulatedVaults:
    def __init__(self,db,paper): self.db,self.paper=db,paper

    @staticmethod
    def empty_limits():
        return dict(allocation='0',positionSize='0',maxLoss='2000000',leverage='1',
                    markets='7',expiresAt='0',autoReduce=False)

    def chain(self,owner):
        network,_=split_owner(owner);chain=adapter(network)
        if not chain.simulated: raise ValueError('DogeOS retains its genuine testnet vault path')
        return chain

    def save(self,owner,v):
        self.db.execute('INSERT OR REPLACE INTO vaults VALUES (?,?,?,?)',(owner,v['address'],json.dumps(v),time.time()))
        return v

    def vault(self,owner):
        chain=self.chain(owner);v=Lending(self.db).vault(owner,False)
        if not v.get('simulated') or v.get('network')!=chain.id: raise ValueError('Simulated vault/network mismatch')
        return v

    def mutate(self,owner,action,body):
        chain=self.chain(owner)
        if body.get('confirm') is not True: raise ValueError('Confirm simulation; no real funds move')
        if action=='create':
            if self.db.execute('SELECT 1 FROM vaults WHERE owner=?',(owner,)).fetchone(): raise ValueError('A vault already exists for this wallet and network')
            asset=body.get('asset',chain.collateral)
            allowed=[chain.collateral]+(['W'+chain.collateral] if chain.family=='evm' else [])
            if asset not in allowed: raise ValueError('Unsupported simulated collateral asset')
            v=dict(address='sim:'+chain.id+':'+uuid.uuid4().hex,owner=owner,network=chain.id,chainId=chain.chain_id,
                simulated=True,asset=asset,illustrativePrice=chain.price,collateral='0',withdrawable='0',
                loanState=0,loanId='',loanNonce=0,principalMicro='0',paused=False,trading=False,
                permissionRevision=0,agent='',settlementSigner='',limits=self.empty_limits())
            self.save(owner,v);Lending(self.db).quote(owner,'0')
        else:
            v=self.vault(owner)
            if body.get('vaultId')!=v['address']: raise ValueError('Matching vault identifier required')
            if action in ('deposit','withdraw'):
                if v['paused']: raise ValueError('Vault paused')
                amount=number(body.get('amount'))
                precision=D('.000000001') if chain.family=='solana' else D('.000000000000000001')
                if amount<=0 or amount!=amount.quantize(precision) or amount>D(1000000): raise ValueError('Invalid demo collateral amount')
                units=int(amount*WEI)
                if action=='deposit':
                    total=number(get(self.db,'demo-collateral:'+owner,'0'))+amount
                    if total>D(1000000): raise ValueError('Demo collateral allocation limit reached')
                    v['collateral']=str(int(v['collateral'])+units)
                    put(self.db,'demo-collateral:'+owner,str(total))
                else:
                    if units>int(v['withdrawable']): raise ValueError('Insufficient unlocked demo collateral')
                    v['collateral']=str(int(v['collateral'])-units)
                if v['loanState']==0: v['withdrawable']=v['collateral']
            elif action=='borrow':
                lending=Lending(self.db);old=lending.loan(owner)
                if v['paused'] or v['loanState']!=0 or old and old['state'] not in ('REPAID','CANCELLED'): raise ValueError('Previous debt must settle before borrowing')
                amount=number(body.get('amount'))
                if amount<=0 or amount!=down(amount): raise ValueError('Invalid borrowing amount')
                # Fresh explicit quote required; never silently refresh stale prices.
                lending.price(owner)
                v.update(loanState=1,loanNonce=v['loanNonce']+1,loanId='sim-loan:'+uuid.uuid4().hex,
                         principalMicro=str(int(amount*1000000)),withdrawable='0',trading=False,limits=self.empty_limits())
                self.save(owner,v);lending.borrow(owner)
                v['loanState']=2
            elif action=='authorize':
                if v['paused']: raise ValueError('Vault paused')
                permission=self.paper.configure(owner,dict(body,confirm=True,positionSize=body.get('positionSize','100'),maxLoss=body.get('maxLoss','2'),leverage=body.get('leverage','1')))
                v.update(agent=owner,settlementSigner=owner,limits=dict(permission,autoReduce=body.get('autoReduce') is True),permissionRevision=v['permissionRevision']+1,trading=True)
            elif action in ('revoke','pause'):
                self.paper.pause_entries(owner)
                from .repayment import Repayment
                Repayment(self.db,self.paper).disable(owner,'Simulation permission revoked or vault paused')
                v.update(trading=False,permissionRevision=v['permissionRevision']+1)
                if action=='pause': v['paused']=not v['paused']
                else:
                    v['limits']=dict(v['limits'],expiresAt='0');v['agent']=''
                    permission=get(self.db,'trading-permission:'+owner,{})
                    if permission: put(self.db,'trading-permission:'+owner,dict(permission,expiresAt='0'))
            else: raise ValueError('Unsupported simulated vault operation')
            self.save(owner,v)
        event(self.db,owner,v.get('loanId') or None,'SIMULATED_VAULT_'+action.upper(),body.get('amount','0'),
              'Local demo accounting; no real token transfer, blockchain receipt or redeemable funds')
        return dict(vault=v,simulated=True,transferable=False,disclosure='Simulation only. No real token transfer or blockchain receipt. Demo funds are not redeemable.')
