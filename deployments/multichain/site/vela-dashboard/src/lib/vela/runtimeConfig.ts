/** Refreshable public capabilities. This never opens a wallet or submits an action. */
type RuntimeConfig = Record<string, unknown> & { chain: { id: 'dogeos'; chainId: 6281971 } };
type State = { config: RuntimeConfig | null; writesEnabled: boolean; error: string };
const address = (v: unknown) => typeof v === 'string' && /^0x[0-9a-f]{40}$/i.test(v) && BigInt(v) !== 0n;
const hash = (v: unknown) => typeof v === 'string' && /^0x[0-9a-f]{64}$/i.test(v) && BigInt(v) !== 0n;
function validate(value: unknown): RuntimeConfig {
  const c = value as RuntimeConfig | null;
  if (!c || c.mode !== 'PAPER' || c.lending !== 'DEMO' || c.liveEnabled !== false || c.realFunding !== false
      || c.chain?.id !== 'dogeos' || c.chain.chainId !== 6281971) throw Error('DogeOS account configuration required');
  return c;
}
function enabled(c: RuntimeConfig) {
  const d = c.nativeRewardDeployment as Record<string, unknown> | undefined;
  return c.nativeVaultActionsEnabled === true && c.nativeWorkflowGatewayEnabled === true && c.attestorConfigured === true
    && !!d && address(d.factory) && [d.factoryCodeHash,d.vaultCodeHash,d.poolCodeHash].every(hash);
}
export class RuntimeConfigStore {
  private state: State = {config:null,writesEnabled:false,error:''};
  private listeners = new Set<()=>void>();
  private generation = 0;
  private pending: Promise<void> | null = null;
  private abort: AbortController | null = null;
  constructor(private readonly read: (signal: AbortSignal)=>Promise<unknown>) {}
  snapshot = () => this.state;
  subscribe = (fn:()=>void) => {this.listeners.add(fn);return ()=>{this.listeners.delete(fn);};};
  private set(next: State) {
    // Unchanged polling results must not recreate a workflow or discard a review.
    if (JSON.stringify(this.state)===JSON.stringify(next)) return;
    this.state=next;this.listeners.forEach(fn=>fn());
  }
  refresh = (): Promise<void> => {
    if (this.pending) return this.pending;
    const generation=this.generation, abort=new AbortController();this.abort=abort;
    const timer=setTimeout(()=>abort.abort(),30000);
    let cancel!: ()=>void;
    const cancelled=new Promise<never>((_resolve,reject)=>{
      cancel=()=>reject(new Error('Activation request cancelled'));
      abort.signal.addEventListener('abort',cancel,{once:true});
    });
    this.pending=(async()=>{
      try {
        const config=validate(await Promise.race([Promise.resolve().then(()=>this.read(abort.signal)),cancelled]));
        if(generation!==this.generation || abort.signal.aborted)return;
        this.set({config,writesEnabled:enabled(config),error:''});
      } catch {
        if(generation===this.generation)this.set({...this.state,writesEnabled:false,error:'Could not refresh activation status. Use Refresh to retry.'});
      } finally {
        clearTimeout(timer);
        abort.signal.removeEventListener('abort',cancel);
        if(generation===this.generation){this.pending=null;this.abort=null;}
      }
    })();
    return this.pending;
  };
  stop() {this.generation++;this.abort?.abort();this.abort=null;this.pending=null;}
}
