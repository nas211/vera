/** Existing Vela API only. No balances, fills, stop prices or debt are created here. */
import { parseDecimal, formatUnits } from '../units';

export type SessionContext = Readonly<{ address: string; network: 'dogeos'; generation: number }>;
export type ApiRequest = { path: string; body?: Readonly<Record<string, unknown>>; key?: string; context: SessionContext; signal: AbortSignal };
export type Transport = (request: ApiRequest) => Promise<unknown>;
export type ResultScope = { realized: string; trades: number; wins: number; losses: number };
export type ServicePosition = { id: string; symbol: string; side: string; qty: string; entry: string; stop: string; riskBudget: string; unrealized: string | null; fresh: boolean; mark?: string | null; funding?: string; entry_fee_left?: string };
export type ServiceFill = { id: string; symbol: string; side: string; pnl: string; closed: number; fees?: string; funding?: string };
export type TradingLimits = { allocation?: string; positionSize?: string; maxLoss?: string; expiresAt?: string };
export type DemoDogeRepayment = {mode:'TESTNET_SIMULATED_CONVERSION';demoDogePerUsd:'1';configured:boolean;ready:boolean;enabled:boolean;reason:string;revision:number;pending:boolean;paidWei:string;reserveAddress:string|null;maximumPerWalletDoge:string;receipts:{hash:string;paidWei:string;block:number}[]};
export type AccountSnapshot = {
  demoDogeRepayment?: DemoDogeRepayment;
  address: string; network: 'dogeos'; vault: { address: string; collateral: string; loanState: number; loanId?: string } | null;
  health: { quote?: { at: number; source: string }; borrowCapacity?: string; debt: string; healthFactor: string | null; loan: { id: string; state: string; principal: string; interest: string } | null;
    ltv?: string | null; interest?: string; principal?: string; collateralValue?: string; price?: string; maxLtv?: string; maxTradingLtv?: string; liquidationThreshold?: string;
    lendingLiquidity?: { currency: string; cash: string; reserved: string; available: string; source: string };
    scenarios?: { drop: string; collateralValue: string; healthFactor: string | null }[] } | null;
  paper: { mode: 'PAPER'; balance: string; equity: string | null; unrealized: string | null; positions: ServicePosition[];
    orders: { id: string; status: string; payload?: {positionId?: string; reduceOnly?: boolean}; error?: string | null }[]; today: ResultScope; totals: ResultScope;
    preferences: { autonomous?: boolean }; entryStop?: { reason: string; action: string } | null;
    trades?: ServiceFill[]; costs?: Record<string, string>; fundingIncomplete?: boolean };
  repayment: { simulated: true; freeEquity: string; maxRepayable: string; debt: string; restriction: string | null; policy: Record<string, unknown> | null };
  tradingPermission: TradingLimits;
  tradingSetup?: { strategy: { id: 'scalp'|'swing'; name: string; description: string; horizon: string }; permissionRevision?: number[]; renewalRequired?: boolean; paperRequired: boolean; nativeRequired: boolean; nativeEnableRequired: boolean; paperLimits: TradingLimits; phase: string; approvedAt: number|null };
  authorizationMaxLossMicro?: string;
  events: { id: number; kind: string; amount: string; detail: string; at?: number }[];
};
export type ServiceAction =
  | { kind: 'allocate' | 'withdraw' | 'repay' | 'repayFull'; amount: string }
  | { kind: 'permission'; allocation: string; positionSize: string; maxLoss: string }
  | { kind: 'strategy'; strategyStyle: 'scalp'|'swing' }
  | { kind: 'sweep'; percent: number; days: number; floor: string }
  | { kind: 'revokeSweep' | 'start' | 'stop' | 'issueBorrow' | 'cancelBorrow' | 'settle' | 'liquidate' | 'refreshQuote' }
  | { kind: 'close'; positionId: string; fraction: '0.5' | '1' }
  | { kind: 'cancel'; orderId: string };
export type ReviewedAction = Readonly<{ path: string; body: Readonly<Record<string, unknown>>; key: string; context: SessionContext; vaultId: string | null; loanId: string | null; tradingLimits?: Readonly<TradingLimits> }>;
export const isTradingControl = (action: ServiceAction) => ['start', 'stop', 'close', 'cancel', 'strategy', 'permission'].includes(action.kind);

const record = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const decimal = (x: unknown) => typeof x === 'string' && /^-?\d+(\.\d+)?$/.test(x);
/** Python Decimal risk ratios may use exponent notation. Expand exactly, never via float. */
function riskDecimal(value: unknown): string {
  if (typeof value !== 'string' || value.length > 100) throw Error('Invalid authoritative risk amount');
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d{1,3}))?$/.exec(value);
  if (!match) throw Error('Invalid authoritative risk amount');
  const [, sign, whole, fraction = '', exponent = '0'] = match;
  const shift = Number(exponent);
  if (Math.abs(shift) > 100) throw Error('Invalid authoritative risk exponent');
  const digits = whole + fraction, point = whole.length + shift;
  const result = point <= 0 ? '0.' + '0'.repeat(-point) + digits : point >= digits.length ? digits + '0'.repeat(point - digits.length) : digits.slice(0, point) + '.' + digits.slice(point);
  if (result.length > 160) throw Error('Invalid authoritative risk precision');
  return sign + result;
}
const same = (a: SessionContext, b: SessionContext) => a.address === b.address && a.network === b.network && a.generation === b.generation;
const identity = (s: AccountSnapshot) => ({ vaultId: s.vault?.address ?? null, loanId: s.health?.loan?.id ?? null });

export function validateAccount(value: unknown, context: SessionContext): AccountSnapshot {
  if (!record(value) || value.address !== context.address || value.network !== context.network) throw new Error('Wallet or network mismatch');
  const p = value.paper, r = value.repayment;
  if (!record(p) || p.mode !== 'PAPER' || !record(r) || r.simulated !== true) throw new Error('PAPER / DEMO account required');
  for (const x of [p.balance, r.freeEquity, r.maxRepayable, r.debt]) if (!decimal(x)) throw new Error('Invalid authoritative amount');
  for (const x of [p.equity, p.unrealized]) if (x !== null && !decimal(x)) throw new Error('Invalid authoritative equity');
  for (const scope of [p.today, p.totals]) {
    if (!record(scope) || !decimal(scope.realized) || ![scope.trades, scope.wins, scope.losses].every(v => Number.isSafeInteger(v) && Number(v) >= 0)) throw new Error('Missing scoped P&L');
  }
  if (!Array.isArray(p.positions) || !Array.isArray(p.orders) || !Array.isArray(value.events) || !record(p.preferences) || !record(value.tradingPermission)) throw new Error('Incomplete account snapshot');
  if (value.authorizationMaxLossMicro !== undefined && (typeof value.authorizationMaxLossMicro !== 'string' || !/^\d+$/.test(value.authorizationMaxLossMicro) || BigInt(value.authorizationMaxLossMicro) > 2_000_000n)) throw new Error('Invalid authorization stop limit');
  for (const position of p.positions) {
    if (!record(position) || typeof position.id !== 'string' || typeof position.symbol !== 'string' || !['long', 'short'].includes(String(position.side)) || ![position.qty, position.entry, position.stop, position.riskBudget].every(decimal)) throw new Error('Invalid authoritative position');
  }
  if (value.vault !== null && (!record(value.vault) || typeof value.vault.address !== 'string' || typeof value.vault.collateral !== 'string')) throw new Error('Invalid vault identity');
  if (value.health !== null && (!record(value.health) || !decimal(value.health.debt) || (value.health.loan !== null && (!record(value.health.loan) || typeof value.health.loan.id !== 'string')))) throw new Error('Invalid loan identity');
  let health = value.health;
  if (record(health)) {
    health = { ...health };
    for (const name of ['ltv','healthFactor','interest','principal','collateralValue','borrowCapacity','price','maxLtv','maxTradingLtv','liquidationThreshold']) {
      const x = health[name]; if (x !== undefined && x !== null) health[name] = riskDecimal(x);
    }
    if (health.lendingLiquidity !== undefined) {
      const pool=health.lendingLiquidity;
      if (!record(pool) || pool.currency !== 'demo USD' || typeof pool.source !== 'string') throw Error('Invalid lending liquidity');
      health.lendingLiquidity={...pool,cash:riskDecimal(pool.cash),reserved:riskDecimal(pool.reserved),available:riskDecimal(pool.available)};
    }
    if (health.scenarios !== undefined) {
      if (!Array.isArray(health.scenarios)) throw Error('Invalid risk scenarios');
      health.scenarios = health.scenarios.map(x => {
        if (!record(x)) throw Error('Invalid risk scenarios');
        return { ...x, drop: riskDecimal(x.drop), collateralValue: riskDecimal(x.collateralValue), healthFactor: x.healthFactor === null ? null : riskDecimal(x.healthFactor) };
      });
    }
  }
  if (p.trades !== undefined && (!Array.isArray(p.trades) || p.trades.some(t => !record(t) || typeof t.id !== 'string' || typeof t.symbol !== 'string' || !decimal(t.pnl) || !Number.isFinite(t.closed)))) throw Error('Invalid closed fill history');
  if (p.costs !== undefined && (!record(p.costs) || !Object.values(p.costs).every(decimal))) throw Error('Invalid reported costs');
  for (const e of value.events) if (!record(e) || !Number.isSafeInteger(e.id) || typeof e.kind !== 'string' || !decimal(e.amount) || typeof e.detail !== 'string') throw Error('Invalid account event');
  return { ...value, health } as AccountSnapshot;
}

function parseUnits(text: string, decimals: number): bigint {
  const result = parseDecimal(text, decimals);
  if (!result.ok) throw new Error(result.error);
  return result.value;
}

function amount(text: string, allowZero = false): string {
  const units = parseUnits(text, 6);
  if (units < 0n || (!allowZero && units === 0n)) throw new Error('Amount must be positive');
  return formatUnits(units, 6);
}

/** All paths are selected here; no arbitrary endpoints, signatures or chain operations. */
export function planAction(snapshot: AccountSnapshot, action: ServiceAction) {
  const financial = identity(snapshot);
  switch (action.kind) {
    case 'strategy':
      if (snapshot.paper.preferences.autonomous) throw Error('Stop trading to change strategy.');
      if (!['scalp','swing'].includes(action.strategyStyle)) throw Error('Choose Scalp or Swing');
      return {path:'/api/trading/strategy',body:{strategyStyle:action.strategyStyle,confirm:true}};
    case 'allocate': case 'withdraw': return { path: `/api/trading/${action.kind}`, body: { amount: amount(action.amount), confirm: true } };
    case 'repay': case 'repayFull': return { path: '/api/repay', body: { ...financial, amount: amount(action.amount), full: action.kind === 'repayFull', confirm: true } };
    case 'permission': {
      const requested = parseUnits(amount(action.maxLoss), 6);
      const prior = snapshot.authorizationMaxLossMicro ?? snapshot.tradingPermission.maxLoss;
      if (prior !== undefined && !/^\d+(\.0+)?$/.test(prior)) throw new Error('Existing stop limit unavailable');
      const existing = prior === undefined ? 2_000_000n : BigInt(prior.split('.')[0]);
      const budget = [requested, existing, 2_000_000n].reduce((a, b) => a < b ? a : b);
      if (budget < 2n) throw new Error('Existing stop limit cannot be raised');
      return { path: '/api/trading/permission', body: { allocation: amount(action.allocation), positionSize: amount(action.positionSize), maxLoss: formatUnits(budget, 6), leverage: '1', confirm: true } };
    }
    case 'sweep':
      if (!Number.isInteger(action.percent) || action.percent < 0 || action.percent > 100 || !Number.isInteger(action.days) || action.days < 1 || action.days > 30) throw new Error('Invalid sweep consent');
      return { path: '/api/repayment/policy', body: { ...financial, enabled: true, sweepPercent: String(action.percent), days: action.days, minimum: amount(action.floor, true), frequency: 'off', source: 'trading', confirm: true } };
    case 'revokeSweep': return { path: '/api/repayment/policy', body: { ...financial, enabled: false, confirm: true } };
    case 'refreshQuote': return { path: '/api/quote', body: { drop: '0' } };
    case 'issueBorrow': case 'cancelBorrow':
      if (!snapshot.vault?.loanId || snapshot.vault.loanState !== 1) throw new Error('Confirmed borrow request required');
      return { path: action.kind === 'issueBorrow' ? '/api/borrow' : '/api/borrow/cancel', body: { vaultId: snapshot.vault.address, loanId: snapshot.vault.loanId, confirm: true } };
    case 'settle': return { path: snapshot.health?.loan && !['REPAID', 'CANCELLED'].includes(snapshot.health.loan.state) ? '/api/settle' : '/api/trading/settle', body: { ...financial, confirm: true } };
    case 'liquidate': return { path: '/api/liquidate', body: { ...financial, confirm: true } };
    case 'start': return { path: '/api/trading/start', body: { confirm: true, ...(snapshot.tradingSetup?.permissionRevision?{permissionRevision:[...snapshot.tradingSetup.permissionRevision]}:{}) } };
    case 'stop': return { path: '/api/trading/stop', body: {} };
    case 'close':
      if (!snapshot.paper.positions.some(p => p.id === action.positionId) || !['0.5', '1'].includes(action.fraction)) throw new Error('Review an owned position');
      return { path: `/api/trading/positions/${encodeURIComponent(action.positionId)}/close`, body: { fraction: action.fraction, confirm: true } };
    case 'cancel':
      if (!snapshot.paper.orders.some(o => o.id === action.orderId && o.status === 'pending')) throw new Error('Review an owned pending order');
      return { path: `/api/trading/orders/${encodeURIComponent(action.orderId)}/cancel`, body: {} };
    default: throw new Error('Unsupported service action');
  }
}

export class AccountService {
  private context: SessionContext | null = null;
  private snapshot: AccountSnapshot | null = null;
  private sequence = 0;
  private controller = new AbortController();
  private issued = new WeakSet<ReviewedAction>();
  private pending = new Map<ReviewedAction, Promise<unknown>>();
  private independent = new WeakMap<ReviewedAction, ServiceAction>();
  constructor(private readonly transport: Transport) {}
  setSession(context: SessionContext | null) {
    this.controller.abort(); this.controller = new AbortController();
    this.context = context ? Object.freeze({ ...context }) : null;
    this.snapshot = null; this.sequence++; this.issued = new WeakSet();
  }
  sessionContext() { return this.active(); }
  async setDemoDogeRepayment(enabled:boolean, revision:number) {
    const context=this.active();
    const result=await this.transport({path:'/api/demo-doge-repayment',body:{confirm:true,enabled,revision},key:crypto.randomUUID(),context,signal:this.controller.signal});
    this.active(context);return result;
  }
  async dogeLending(path: 'config' | 'account', tx?: string) {
    if (tx && !/^0x[0-9a-f]{64}$/i.test(tx)) throw Error('Invalid transaction hash');
    const context = this.active();
    const value = await this.transport({path:'/api/doge-lending/'+path+(tx?'?tx='+tx:''),context,signal:this.controller.signal});
    this.active(context); return value;
  }
  async nativeState(tx?: string) {
    if (tx && !/^0x[0-9a-f]{64}$/i.test(tx)) throw new Error('Invalid transaction hash');
    const context = this.active();
    const result = await this.transport({ path: '/api/vault/state' + (tx ? '?tx=' + tx : ''), context, signal: this.controller.signal });
    this.active(context); return result;
  }
  async authorizeMigration(migration: { sourceVault: string; targetFactory: string; targetVault: string }) {
    const context=this.active();
    const result=await this.transport({path:'/api/vault/migration/authorize',body:{confirm:true,sourceVault:migration.sourceVault,targetFactory:migration.targetFactory,targetVault:migration.targetVault},context,signal:this.controller.signal});
    this.active(context);return result;
  }
  async nativeCertificate(vaultId: string, loanId: string) {
    const context = this.active();
    const result = await this.transport({ path: '/api/certificate', body: { vaultId, loanId }, context, signal: this.controller.signal });
    this.active(context); return result;
  }
  async syncNative(tx?: string, includeState = false) {
    if (tx !== undefined && !/^0x[0-9a-f]{64}$/i.test(tx)) throw new Error('Invalid transaction hash');
    const context = this.active();
    const result = await this.transport({ path: '/api/vault/sync', body: { ...(tx ? {tx} : {}), ...(includeState ? {includeState:true} : {}) }, key: 'native-sync:' + (tx ?? 'state'), context, signal: this.controller.signal });
    this.active(context); this.snapshot = null; this.sequence++; return result;
  }
  private active(expected?: SessionContext) {
    const c = this.context;
    if (!c || c.network !== 'dogeos' || !/^0x[0-9a-f]{40}$/.test(c.address) || (expected && !same(c, expected))) throw new Error('Authenticated wallet context changed');
    return c;
  }
  async refresh() {
    const context = this.active(), seq = ++this.sequence;
    this.snapshot = null;
    const value = await this.transport({ path: '/api/account', context, signal: this.controller.signal });
    this.active(context);
    if (seq !== this.sequence) throw new Error('Account read superseded');
    const snapshot = validateAccount(value, context);
    this.snapshot = structuredClone(snapshot);
    return snapshot;
  }
  /** Fresh authenticated read, independent of the native-sync cache generation. */
  async inspect() {
    const context = this.active();
    const value = await this.transport({path:'/api/account', context, signal:this.controller.signal});
    this.active(context);
    return validateAccount(value, context);
  }
  async prepareTrading(action: ServiceAction, key: string) {
    if (!isTradingControl(action)) throw Error('Trading control required');
    const frozenAction = structuredClone(action), snapshot = await this.inspect();
    const request = this.issue(snapshot, frozenAction, key);
    this.independent.set(request, frozenAction);
    return {snapshot, request};
  }
  review(action: ServiceAction, key: string): ReviewedAction {
    if (!this.snapshot) throw new Error('Refresh account before reviewing an action');
    return this.issue(this.snapshot, action, key);
  }
  private issue(snapshot: AccountSnapshot, action: ServiceAction, key: string): ReviewedAction {
    const context = this.active();
    if (!/^[\w:-]{8,160}$/.test(key)) throw new Error('Stable idempotency key required');
    const plan = planAction(snapshot, action);
    const reviewed = Object.freeze({ ...plan, body: Object.freeze(plan.body), key, context, ...identity(snapshot), ...(action.kind==='start'?{tradingLimits:Object.freeze({...snapshot.tradingSetup?.paperLimits??snapshot.tradingPermission})}:{}) });
    this.issued.add(reviewed);
    return reviewed;
  }
  execute(reviewed: ReviewedAction, confirmed: boolean): Promise<unknown> {
    if (!confirmed || !this.issued.has(reviewed)) return Promise.reject(new Error('Confirm the reviewed action'));
    const pending = this.pending.get(reviewed);
    if (pending) return pending;
    const run = this.submit(reviewed).finally(() => this.pending.delete(reviewed));
    this.pending.set(reviewed, run);
    return run;
  }
  private async submit(reviewed: ReviewedAction) {
    this.active(reviewed.context);
    const control = this.independent.get(reviewed);
    const current = control ? await this.inspect() : await this.refresh();
    const ids = identity(current);
    const resumedBorrow = ['/api/borrow', '/api/borrow/cancel'].includes(reviewed.path) && current.vault?.loanId === reviewed.body.loanId;
    if (ids.vaultId !== reviewed.vaultId || (!resumedBorrow && ids.loanId !== reviewed.loanId)) throw new Error('Loan or vault changed — review again');
    this.active(reviewed.context);
    this.sequence++; // Older reads cannot restore stale account status.
    this.snapshot = null;
    const result = await this.transport({ ...reviewed, signal: this.controller.signal });
    this.active(reviewed.context);
    this.sequence++;
    return result; // UI refreshes authoritative balances; no optimistic financial mutation.
  }
}
