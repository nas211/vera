"""Read-only, hash-checked original decision methods; never run its I/O main."""
import builtins, hashlib, json, types
from pathlib import Path

def load():
    root=Path(__file__).resolve().parents[1]/'vendor'
    expected=json.loads((root/'sha256.json').read_text()); modules={}
    def importing(name, globals=None, locals=None, fromlist=(), level=0):
        if name in modules and not level:return modules[name]
        # Original runner imports these only for its unused network main.
        if name in ('requests','websocket'):return types.ModuleType(name)
        return builtins.__import__(name,globals,locals,fromlist,level)
    for name in ('runner','v2','overnight','maker_only','profit_capture'):
        path=root/(name+'.py');code=path.read_bytes()
        if hashlib.sha256(code).hexdigest()!=expected[path.name]:raise ValueError('baseline source hash mismatch')
        mod=types.ModuleType('frozen_'+name);mod.__file__=str(path)
        mod.__dict__['__builtins__']=dict(vars(builtins),__import__=importing)
        exec(compile(code,str(path),'exec'),mod.__dict__);modules[name]=mod
    modules['profit_capture'].configure()
    return modules
MODULES=load()
BASE=MODULES['profit_capture'].ProfitCapturePaper
