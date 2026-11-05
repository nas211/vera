"""Explicit owner consent for an empty legacy vault; no funds or loans are moved."""
import json
import time
from decimal import Decimal
from vela.db import get, put
from .config import CHAIN_ID
from .store import event

ZERO = '0x' + '00' * 20


def context(db, owner):
    value = get(db, 'native-vault-migration:' + owner)
    return {'migration': value} if value else {}


def empty_legacy(v):
    return bool(v and v.get('kind') == 'legacy' and v.get('bytecodeVerified')
        and v.get('collateral') == '0' and v.get('withdrawable') == '0'
        and v.get('loanState') in (0, 3) and v.get('badDebtMicro') == '0'
        and not v.get('paused') and not v.get('trading') and v.get('agent', '').lower() == ZERO
        and all(str(v.get('limits', {}).get(k)) == '0' for k in
                ('allocation', 'positionSize', 'expiresAt', 'maxLoss', 'leverage', 'markets'))
        and v.get('limits', {}).get('autoReduce') is False)


def idle_ledger(db, owner):
    if db.execute("SELECT 1 FROM loans WHERE owner=? AND (state NOT IN ('REPAID','CANCELLED') OR CAST(principal AS NUMERIC)!=0 OR CAST(interest AS NUMERIC)!=0 OR CAST(bad_debt AS NUMERIC)!=0)", (owner,)).fetchone():
        raise ValueError('Settle every loan before moving to the reward vault')
    if (db.execute('SELECT 1 FROM positions WHERE user=?', (owner,)).fetchone()
            or db.execute("SELECT 1 FROM paper_orders WHERE user=? AND status='pending'", (owner,)).fetchone()
            or get(db, 'paper-prefs:' + owner, {}).get('autonomous')
            or get(db, 'direct-settlement:' + owner, False)
            or get(db, 'paper-funding-incomplete:' + owner, False)):
        raise ValueError('Stop trading and reconcile positions, orders and funding before migration')


def legacy_budget(v):
    # An initial settled/cancelled loan has exactly two/one revocations. Any
    # authorization increments revision as well. Never guess at erased limits.
    unused = ((v['loanNonce'] == 0 and v['permissionRevision'] == 0 and v['loanState'] == 0)
              or (v['loanNonce'] == 1 and v['permissionRevision'] in (1, 2) and v['loanState'] == 3))
    return 2_000_000 if unused else 0


def authorize(db, owner, observed, body):
    v, upgrade = observed.get('vault'), observed.get('upgrade')
    if body.get('confirm') is not True or not empty_legacy(v) or not upgrade or not upgrade['eligible']:
        raise ValueError('Withdraw all legacy collateral and confirm settlement before migration')
    if (observed['owner'] != owner or observed['chainId'] != CHAIN_ID
            or body.get('sourceVault') != v['address'] or body.get('targetFactory') != upgrade['targetFactory']
            or body.get('targetVault') != upgrade['targetVault']):
        raise ValueError('Reviewed migration addresses changed')
    stored = db.execute('SELECT data FROM vaults WHERE owner=?', (owner,)).fetchone()
    if not stored or json.loads(stored[0])['address'] != v['address']:
        raise ValueError('Synchronize the existing vault before migration')
    if json.loads(stored[0])['block'] > observed['block']:
        raise ValueError('Older migration observation discarded')
    idle_ledger(db, owner)
    ceiling = min(Decimal(legacy_budget(v)), Decimal(get(db, 'account-stop-budget:' + owner, '2000000')),
                  Decimal(get(db, 'trading-permission:' + owner, {}).get('maxLoss', '2000000')))
    intent = dict(version=1, owner=owner, chainId=CHAIN_ID, sourceVault=v['address'],
                  targetFactory=upgrade['targetFactory'], targetVault=upgrade['targetVault'],
                  sourceNonce=v['loanNonce'], sourceRevision=v['permissionRevision'],
                  stopBudgetMicro=str(int(ceiling)), authorizedAt=time.time())
    old = get(db, 'native-vault-migration:' + owner)
    if old:
        if any(old[k] != intent[k] for k in ('owner', 'chainId', 'sourceVault', 'targetFactory', 'targetVault', 'sourceNonce', 'sourceRevision')):
            raise ValueError('Existing migration intent differs; reconcile it first')
        return old
    put(db, 'native-vault-migration:' + owner, intent)
    put(db, 'account-stop-budget:' + owner, str(int(ceiling)))
    event(db, owner, v['loanId'], 'REWARD_VAULT_MIGRATION_AUTHORIZED', detail=json.dumps(intent))
    return intent


def verify_switch(db, owner, old, new):
    intent = get(db, 'native-vault-migration:' + owner)
    source = new.get('migratedFrom')
    if (not intent or intent['owner'] != owner or intent['chainId'] != CHAIN_ID
            or old['address'] != intent['sourceVault'] or new['address'] != intent['targetVault']
            or new.get('factory') != intent['targetFactory'] or new.get('kind') != 'rewarded'
            or not new.get('bytecodeVerified') or not empty_legacy(source)
            or source['address'] != old['address'] or source['block'] != new['block']
            or source['blockHash'] != new['blockHash'] or source['owner'] != owner
            or source['loanNonce'] != intent['sourceNonce'] or source['permissionRevision'] != intent['sourceRevision']):
        raise ValueError('Explicit verified migration required to replace a registered vault')
    idle_ledger(db, owner)
    cap = min(Decimal(intent['stopBudgetMicro']), Decimal(get(db, 'account-stop-budget:' + owner, '2000000')))
    put(db, 'account-stop-budget:' + owner, str(int(cap)))
    event(db, owner, None, 'REWARD_VAULT_MIGRATED', detail=json.dumps(dict(source=old['address'], target=new['address'], block=new['block'])))
