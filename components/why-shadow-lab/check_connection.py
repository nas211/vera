#!/usr/bin/env python3
"""Read-only authenticated model-availability check. Never print credentials."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

def check():
    key=os.environ.get("WHY_LAB_LLM_KEY","")
    if not key:
        # This is the known, owner-authorized lab credential, not discovery of
        # any existing bot secrets. The root-only file is never dumped/logged.
        for line in Path("/etc/why-shadow-lab/model.env").read_text().splitlines():
            if line.startswith("WHY_LAB_LLM_KEY="):key=line.partition("=")[2].strip()
    if not key:raise RuntimeError("lab key missing")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise RuntimeError("redirect prohibited")
    request=urllib.request.Request("https://api.groq.com/openai/v1/models",headers={
        "Authorization":"Bearer "+key,"User-Agent":"why-shadow-lab/1.0","Accept":"application/json"})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(request,timeout=20) as response:
            result=json.loads(response.read(262144))
            ids={row["id"] for row in result.get("data",[])}
            print(json.dumps(dict(http_status=response.status,selected_model_available="openai/gpt-oss-120b" in ids,available_models=len(ids))))
    except urllib.error.HTTPError as exc:
        body=exc.read(32768).decode("utf-8","replace").replace(key,"[REDACTED]")
        try:
            result=json.loads(body);error=result.get("error",{})
            if not isinstance(error,dict):error={"message":str(error)}
            detail={k:str(error[k])[:300] for k in ("type","code","message") if k in error}
        except ValueError:detail={"body_excerpt":body[:300]}
        print(json.dumps(dict(http_status=exc.code,error=detail)))
    except (urllib.error.URLError,TimeoutError):print(json.dumps(dict(error="connection_failed")))

if __name__=="__main__":check()
