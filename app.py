import json, os, time, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
API="https://external-api.kalshi.com/trade-api/v2"
def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":"BTC15PaperMonitor/1.0","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=9) as r:return json.load(r)
def price(x):
    if x is None:return None
    try:
        v=float(x)
        return round(v/100,4) if v>1 else round(v,4)
    except:return None
def snapshot():
    data=fetch(API+"/markets?series_ticker=KXBTC15M&status=open&limit=100")
    now=time.time()
    ms=[m for m in data.get("markets",[]) if m.get("close_time") and m.get("status")=="active" or m.get("status")=="open"]
    ms=sorted(ms,key=lambda m:m.get("close_time",""))
    for m in ms:
        if True:
            from datetime import datetime
            close=datetime.fromisoformat(m["close_time"].replace("Z","+00:00")).timestamp()
            if close<=now:continue
            strike=m.get("floor_strike")
            if strike is None:continue
            ticker=m["ticker"]
            ob=fetch(API+"/markets/"+urllib.parse.quote(ticker,safe="")+"/orderbook").get("orderbook",{})
            def best(side):
                levels=ob.get(side+"_dollars") or ob.get(side) or []
                if not levels:return None
                vals=[price(p[0]) if side+"_dollars" not in ob else float(p[0]) for p in levels]
                return max(vals)
            yb,nb=best("yes"),best("no")
            return {"ticker":ticker,"strike":strike,"close_time":m["close_time"],"seconds_left":round(close-now),"yes_ask":round(1-nb,4) if nb is not None else None,"no_ask":round(1-yb,4) if yb is not None else None,"source":"Kalshi public market API","asof":int(now)}
    raise ValueError("No active KXBTC15M contract with a published strike")
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith("/api/snapshot"): 
                try: payload={"ok":True,"data":snapshot()}
                except Exception as e:payload={"ok":False,"error":str(e)[:180]}
                body=json.dumps(payload).encode();typ="application/json"
            else:
                body=Path(__file__).with_name("index.html").read_bytes();typ="text/html; charset=utf-8"
            self.send_response(200);self.send_header("Content-Type",typ);self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body)
        except Exception:
            self.send_error(500)
if __name__=="__main__":
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),Handler).serve_forever()
