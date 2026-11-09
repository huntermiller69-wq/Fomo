import json, os, time, urllib.request, urllib.parse, sqlite3, threading, math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
DB=os.getenv('DB_PATH','/tmp/btc15_paper.sqlite3')
LOCK=threading.Lock()
def connect():
    c=sqlite3.connect(DB,timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS trades (ticker TEXT PRIMARY KEY, side TEXT, entry REAL, contracts INTEGER, cost REAL, opened INTEGER, close_time TEXT, status TEXT, result TEXT, pnl REAL, model_probability REAL, spot REAL, strike REAL)')
    c.commit();return c

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
def spot():
    d=fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot")
    return float(d["data"]["amount"])
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
            return {"spot":safe_spot(),"spread":round((1-nb)+(1-yb)-1,4) if nb is not None and yb is not None else None,"ticker":ticker,"strike":strike,"close_time":m["close_time"],"seconds_left":round(close-now),"yes_ask":round(1-nb,4) if nb is not None else None,"no_ask":round(1-yb,4) if yb is not None else None,"source":"Kalshi public market API","asof":int(now)}
    raise ValueError("No active KXBTC15M contract with a published strike")
def model_probability(d):
    if d.get("spot") is None or d.get("seconds_left",0)<60:return None
    # EXPERIMENTAL normal approximation; not trained on historical BRTI settlements.
    seconds=d["seconds_left"]
    sigma=0.0007*math.sqrt(seconds/900)
    z=math.log(float(d["spot"])/float(d["strike"]))/max(sigma,0.00001)
    return max(.01,min(.99,.5*(1+math.erf(z/math.sqrt(2)))))
PAPER_ENTRIES_ENABLED = False  # paused until Coinbase contract, benchmark, quotes and fees are reconciled
def paper_cycle():
    with LOCK:
        c=connect()
        try:
            rows=c.execute("SELECT ticker,side,entry,contracts,cost FROM trades WHERE status='open'").fetchall()
            for ticker,side,entry,n,cost in rows:
                try:
                    m=fetch(API+"/markets/"+urllib.parse.quote(ticker,safe="")).get("market",{})
                    result=str(m.get("result") or "").lower()
                    if result not in ("yes","no"):continue
                    pnl=round((n if side==result else 0)-cost,2)
                    c.execute("UPDATE trades SET status='settled',result=?,pnl=? WHERE ticker=?",(result,pnl,ticker))
                    c.commit()
                except Exception:continue
            try:
                if not PAPER_ENTRIES_ENABLED: return
                d=snapshot();p=model_probability(d)
                if p is not None and d["seconds_left"]>=120:
                    # 8 percentage-point minimum gross model edge; no assumed fee discount.
                    choices=[("yes",p,d["yes_ask"]),("no",1-p,d["no_ask"])]
                    choices=[x for x in choices if x[2] is not None and .05<=x[2]<=.95 and x[1]-x[2]>=.08]
                    if choices:
                        side,prob,ask=max(choices,key=lambda x:x[1]-x[2]);n=int(10/ask)
                        if n>0:
                            cost=round(n*ask,2)
                            c.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(d["ticker"],side,ask,n,cost,int(time.time()),d["close_time"],"open",None,None,round(prob,4),d["spot"],d["strike"]))
                            c.commit()
            except Exception:pass
        finally:c.close()
def stats():
    c=connect()
    try:
        rows=c.execute("SELECT ticker,side,entry,contracts,cost,opened,status,result,pnl,model_probability FROM trades ORDER BY opened DESC LIMIT 100").fetchall()
        settled=[r for r in rows if r[6]=="settled"]
        return {"starting_balance":1000,"settled_count":len(settled),"wins":sum(r[8]>0 for r in settled),"net_pnl":round(sum(r[8] for r in settled),2),"open_count":sum(r[6]=="open" for r in rows),"trades":[dict(zip(("ticker","side","entry","contracts","cost","opened","status","result","pnl","model_probability"),r)) for r in rows],"storage":"temporary; resets if hosting instance is replaced","fees":"not included; reported profit is gross, not net","entries_paused":True,"verification":"UNVERIFIED - not Coinbase-reconciled"}
    finally:c.close()
def worker():
    while True:
        try:paper_cycle()
        except Exception:pass
        time.sleep(20)

def safe_spot():
    try:return spot()
    except:return None
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith("/api/stats"):
                payload={"ok":True,"data":stats()};body=json.dumps(payload).encode();typ="application/json"
            elif self.path.startswith("/api/snapshot"): 
                try: payload={"ok":True,"data":snapshot()}
                except Exception as e:payload={"ok":False,"error":str(e)[:180]}
                body=json.dumps(payload).encode();typ="application/json"
            else:
                body=Path(__file__).with_name("index.html").read_bytes();typ="text/html; charset=utf-8"
            self.send_response(200);self.send_header("Content-Type",typ);self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(body)
        except Exception:
            self.send_error(500)
if __name__=="__main__":
    threading.Thread(target=worker,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),Handler).serve_forever()
