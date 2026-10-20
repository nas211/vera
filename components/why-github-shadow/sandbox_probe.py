"""Exercise the deployed OS boundary without calling any private upstream endpoint."""
import json
from pathlib import Path
import socket
from run_shadow import UnixHTTP

checks={}
try:
    socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    checks['ip_socket_denied']=False
except OSError:
    checks['ip_socket_denied']=True
for name,path in [('production_hidden','/opt/why-yellow'),('home_hidden','/root'),
                  ('host_run_hidden','/run/dbus/system_bus_socket')]:
    try:
        p=Path(path)
        checks[name]=not p.exists() or (p.is_dir() and not list(p.iterdir()))
    except PermissionError:
        checks[name]=True
for label,method,path,expected in [
    ('public_data_works','GET','/v5/market/time',200),
    ('private_route_denied','GET','/v5/account/wallet-balance',403),
    ('order_post_denied','POST','/v5/order/create',501)]:
    c=UnixHTTP('public-relay',timeout=30)
    try:
        c.request(method,path);r=c.getresponse();r.read();checks[label]=r.status==expected
    finally:c.close()
dest=Path('/opt/why-github-shadow/state/SandboxProbe/result.json')
dest.write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks),flush=True)
if not all(checks.values()):raise SystemExit(1)
