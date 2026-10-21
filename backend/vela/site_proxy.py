"""Trust public-origin headers only from an authenticated Vela reverse proxy."""
import hmac
import ipaddress
import json
import re
from urllib.parse import urlsplit


class SiteProxyMiddleware:
    def __init__(self,app,origin,token=""):
        self.app=app
        self.origin=origin.rstrip("/")
        parsed=urlsplit(self.origin)
        if (parsed.scheme not in ("http","https") or not parsed.hostname or
                parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Invalid public application origin")
        if parsed.scheme=="http" and parsed.hostname not in ("127.0.0.1","localhost","testserver"):
            raise ValueError("Public application requires HTTPS")
        self.host=parsed.netloc
        self.secure=parsed.scheme=="https"
        self.token=token
        if token and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}",token):
            raise ValueError("Site proxy token must contain 32–256 URL-safe characters")

    async def __call__(self,scope,receive,send):
        if scope["type"] not in ("http","websocket"):
            return await self.app(scope,receive,send)
        headers=dict(scope.get("headers",[]))
        supplied=headers.get(b"x-vela-site-token",b"")
        signed=bool(self.token and hmac.compare_digest(supplied,self.token.encode("ascii")))
        protected=any(scope["path"]==p or scope["path"].startswith(p+"/")
                      for p in ("/api/portal","/api/paper"))
        if supplied or (self.secure and protected):
            valid_origin=headers.get(b"x-vela-public-origin")==self.origin.encode("ascii")
            if not signed or not valid_origin:
                return await self.reject(scope,send,503 if not self.token else 403)
        if signed:
            scope=dict(scope)
            # Never accept arbitrary X-Forwarded-Host/Proto or visitor IP headers.
            clean=[(k,v) for k,v in scope["headers"] if k not in
                   (b"host",b"forwarded",b"x-forwarded-host",b"x-forwarded-proto",
                    b"x-forwarded-for",b"x-vela-site-token",b"x-vela-public-origin",b"x-vela-client-ip")]
            scope["headers"]=clean+[(b"host",self.host.encode("ascii"))]
            scope["scheme"]=("wss" if self.secure else "ws") if scope["type"]=="websocket" else ("https" if self.secure else "http")
            try:
                client=str(ipaddress.ip_address(headers.get(b"x-vela-client-ip",b"").decode("ascii")))
                scope["client"]=(client,0)
            except (ValueError,UnicodeError):
                pass
        return await self.app(scope,receive,send)

    @staticmethod
    async def reject(scope,send,status):
        if scope["type"]=="websocket":
            return await send({"type":"websocket.close","code":1013 if status==503 else 1008})
        body=json.dumps({"detail":"Portal connection is not configured" if status==503 else "Invalid portal connection"}).encode()
        await send({"type":"http.response.start","status":status,"headers":[
            (b"content-type",b"application/json"),(b"cache-control",b"no-store")]})
        await send({"type":"http.response.body","body":body})
