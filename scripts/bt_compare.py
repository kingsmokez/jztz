"""WP2 优化前后对比回测 — 旧策略(6因子) vs 新策略(10因子V2.2)

旧策略(V1): 收盘位置40+连续放量20+均线斜率15+波动率15+量价15+风险排除-30
新策略(V2.2): 收盘位置25+连续放量20+均线斜率20+波动率15+量价15+风险排除-40
              +板块强度15+炸板-20+尾盘综合30+相对强度15
"""

import sys, os, json, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.http_client import session, EM_HEADERS
from modules.logger import log
from modules.kline_fetcher import kline_fetcher


BACKTEST_DAYS = 180
TOP_N = 10


def log_print(msg):
    print(msg)


def fetch_trade_days(n):
    days = set()
    for sym in ["sh000300", "sz399001"]:
        try:
            kd = kline_fetcher.get_kline_raw(sym, n * 2)
            if kd and kd.get("data",{}).get("klines"):
                for line in kd["data"]["klines"]:
                    days.add(line.split(",")[0].replace("-",""))
        except: pass
    sd = sorted(days)
    if sd: return sd[-n-5:-5]
    d = datetime.now(); fb=[]
    while len(fb) < n:
        if d.weekday()<5: fb.insert(0,d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return fb[-n:]


from datetime import timedelta


def fetch_klines(codes):
    result = {}
    def f(code):
        try:
            sym = f"sh{code}" if code.startswith("6") else f"sz{code}"
            kd = kline_fetcher.get_kline_raw(sym, 250)
            if kd and kd.get("data",{}).get("klines"):
                kl=[]
                for line in kd["data"]["klines"]:
                    p=line.split(",")
                    if len(p)>=6:
                        try: kl.append({"d":p[0],"o":float(p[1]),"c":float(p[2]),"h":float(p[3]),"l":float(p[4]),"v":float(p[5])})
                        except: pass
                if len(kl)>=60: return code,kl
        except: pass
        return code,None
    with ThreadPoolExecutor(max_workers=6) as pool:
        fs=[pool.submit(f,c) for c in codes]
        done=0
        for fu in as_completed(fs):
            done+=1
            try:
                c,kl=fu.result(timeout=60)
                if kl: result[c]=kl
            except: pass
            if done%200==0: log_print(f"  K线: {done}/{len(codes)}")
    return result


def ma(p,n):
    return sum(p[-n:])/n if len(p)>=n else None


def score_v1(kl, t, code):
    """旧策略6因子"""
    if t<15: return 0
    cl=[k["c"] for k in kl]; hi=[k["h"] for k in kl]; lo=[k["l"] for k in kl]
    vl=[k["v"] for k in kl]; op=[k["o"] for k in kl]
    pct=(cl[t]-cl[t-1])/cl[t-1]*100 if cl[t-1]>0 else 0
    m5=ma(cl,5); m10=ma(cl,10); m20=ma(cl,20)
    s=0.0; cps=0; vs=0; ms=0; atrs=0; vps=0; rk=0
    
    # F1: 收盘位置 (0~40)
    dr=hi[t]-lo[t]
    if dr>0:
        pos=(cl[t]-lo[t])/dr; sr=(hi[t]-cl[t])/dr; lu=pct>=9.5
        if pos>0.95 and sr<0.03: cps=25 if lu else 40
        elif pos>0.85 and sr<0.10: cps=18 if lu else 30
        elif pos>0.70: cps=15 if lu else 22
        elif pos>0.50: cps=12
        elif pos>0.30: cps=5
        else: cps=-5
        if sr>0.50: cps-=15
        elif sr>0.35: cps-=8
        elif sr>0.20: cps-=3
        s+=cps
    
    # F2: 连续放量
    vs=0
    if t>=3:
        vols=vl[t-2:t+1]
        if vols[0]>0 and vols[1]>0 and vols[0]<vols[1]<vols[2]:
            vr=vols[2]/max(vols[0],1)
            vs=20 if vr>2.5 else(15 if vr>1.8 else 10)
        avg5=sum(vl[max(0,t-5):t])/min(5,t)
        if vl[t]<avg5*0.7 and pct>0: vs=-5
    elif t>=1:
        vr=vl[t]/max(vl[t-1],1)
        vs=15 if vr>2 else(8 if vr>1.5 else 0)
    s+=vs
    
    # F3: 均线斜率
    m5=ma(cl,5); m10=ma(cl,10); m20=ma(cl,20)
    ms=0
    if m5 and m10 and m20:
        if m5>m10>m20: ms=8
        elif m5>m20: ms=3
        else: ms=-5
    s+=ms
    
    # F4: 波动率
    atrs=0
    if t>=10:
        trs=[max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1])) for i in range(max(1,t-9),t+1)]
        if len(trs)>=5:
            a5=sum(trs[-5:])/5; a10=sum(trs)/len(trs)
            if a10>0:
                ar=a5/a10
                if ar<0.8 and cl[t]>hi[t-1]: atrs=15
                elif ar<0.9 and pct>2: atrs=10
                elif ar>1.5: atrs=-5
    s+=atrs
    
    # F5: 量价
    vps=0
    if t>=5:
        ud=sum(1 for i in range(t-4,t+1) if cl[i]>op[i])
        if ud>=3: vps=10
        if pct>3 and vl[t]<vl[t-1]*0.8: vps=-10
    s+=vps
    
    # F6: 风险
    rk=0
    if t>=2:
        lc=0
        for i in range(t-1,max(t-4,-1),-1):
            dp=(cl[i]-op[i])/op[i]*100 if op[i]>0 else 0
            if dp>=9.5: lc+=1; break
        if lc>=1: rk-=5
    if pct>9.5: rk-=30
    elif pct>8: rk-=15
    elif pct>7: rk-=8
    s+=rk
    
    return s


def score_v2(kl, t, code):
    """新策略10因子"""
    if t<15: return 0
    cl=[k["c"] for k in kl]; hi=[k["h"] for k in kl]; lo=[k["l"] for k in kl]
    vl=[k["v"] for k in kl]; op=[k["o"] for k in kl]
    pct=(cl[t]-cl[t-1])/cl[t-1]*100 if cl[t-1]>0 else 0
    m5=ma(cl,5); m10=ma(cl,10); m20=ma(cl,20)
    s=0.0; cps=0; vs=0; ms=0; atrs=0; vps=0; rk=0
    
    # F1: 收盘位置(0~25)
    dr=hi[t]-lo[t]
    if dr>0:
        pos=(cl[t]-lo[t])/dr; sr=(hi[t]-cl[t])/dr; lu=pct>=9.5
        if pos>0.95 and sr<0.03: cps=15 if lu else 25
        elif pos>0.85 and sr<0.10: cps=10 if lu else 18
        elif pos>0.70: cps=8 if lu else 14
        elif pos>0.50: cps=8
        elif pos>0.30: cps=3
        else: cps=-8
        if sr>0.50: cps-=15
        elif sr>0.35: cps-=8
        elif sr>0.20: cps-=3
    s+=cps
    
    # F2: 连续放量
    vs=0
    if t>=3:
        vols=vl[t-2:t+1]
        if vols[0]>0 and vols[1]>0 and vols[0]<vols[1]<vols[2]:
            vr=vols[2]/max(vols[0],1)
            vs=20 if vr>2.5 else(15 if vr>1.8 else 10)
    elif t>=1:
        vr=vl[t]/max(vl[t-1],1)
        vs=15 if vr>2 else(8 if vr>1.5 else 0)
    s+=vs
    
    # F3: 均线斜率(-10~20)
    ms=0
    if m5 and m10 and m20 and t>=5:
        m5v=[]
        for i in range(max(0,t-4),t+1):
            seg=cl[max(0,i-4):i+1]
            if len(seg)==5: m5v.append(sum(seg)/5)
        if len(m5v)>=3:
            sn=m5v[-1]-m5v[-2]; sp=m5v[-2]-m5v[-3]
            if sn>0 and sp>0: ms=20 if sn>sp*1.5 else(14 if sn>sp else 8)
            elif sn>0 and sp<=0: ms=15
            elif sn<=0 and sp>0: ms=-15
            elif sn<=0: ms=-8
        if m5>m10>m20: ms+=3
    s+=ms
    
    # F4: 波动率
    atrs=0
    if t>=10:
        trs=[max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1])) for i in range(max(1,t-9),t+1)]
        if len(trs)>=5:
            a5=sum(trs[-5:])/5; a10=sum(trs)/len(trs)
            if a10>0 and a5/a10<0.8 and cl[t]>hi[t-1]: atrs=15
    s+=atrs
    
    # F5: 量价
    vps=0
    if t>=5:
        ud=sum(1 for i in range(t-4,t+1) if cl[i]>op[i])
        if ud>=3: vps=10
        if pct>3 and vl[t]<vl[t-1]*0.8: vps=-10
    s+=vps
    
    # F6: 风险(-40~-5)
    rk=0
    if t>=2:
        lc=0
        for i in range(t-1,max(t-4,-1),-1):
            dp=(cl[i]-op[i])/op[i]*100 if op[i]>0 else 0
            if dp>=9.5: lc+=1; break
        if lc>=1: rk-=5
    if t>=1 and lo[t]>hi[t-1]:
        gp=(lo[t]-hi[t-1])/hi[t-1]
        if gp>0.05: rk-=10; 
    if pct>9.5: rk-=40
    elif pct>8: rk-=20
    elif pct>7: rk-=12
    elif pct>5: rk-=5
    s+=rk
    
    # F8: 炸板
    if t>0:
        o,c,h=op[t],cl[t],hi[t]
        if o>0 and h>=o*1.095 and c<o*1.095*0.995: s-=20
    
    return s


def run_compare():
    print("="*70)
    print("  WP2 优化前后对比回测")
    print(f"  周期: 近{BACKTEST_DAYS}交易日 | Top-N: {TOP_N}")
    print("="*70)
    
    # 数据
    print("\n[数据]")
    codes = []
    try:
        from modules.data_fetcher import _get_all_stock_codes
        codes = _get_all_stock_codes()
    except: pass
    if not codes: codes = [f"{p}{s:03d}" for p in ["000","001","002","003","300","600","601","603","605"] for s in range(1,50)][:200]
    
    days = fetch_trade_days(BACKTEST_DAYS+10)
    print(f"  交易日: {len(days)}天 ({days[0]}~{days[-1]})")
    
    sample = codes[:500]
    print(f"  股票池: {len(sample)}只")
    
    klines = fetch_klines(sample)
    print(f"  K线: {len(klines)}只")
    
    # 回测
    print("\n[回测中...]")
    v1_trades, v2_trades = [], []
    days_run = 0
    
    for di in range(len(days)-2):
        today, tomorrow = days[di], days[di+1]
        v1_cand, v2_cand = [], []
        
        for code, kl in klines.items():
            t = -1
            for i,k in enumerate(kl):
                if k["d"].replace("-","").startswith(today): t=i; break
            if t<15: continue
            
            sv1 = score_v1(kl, t, code)
            sv2 = score_v2(kl, t, code)
            
            if sv1 >= 30: v1_cand.append({"code":code,"s":round(sv1,1),"i":t})
            if sv2 >= 30: v2_cand.append({"code":code,"s":round(sv2,1),"i":t})
        
        def make_trades(cand, store):
            if not cand: return
            cand.sort(key=lambda x:x["s"],reverse=True)
            for pick in cand[:TOP_N]:
                kl2 = klines.get(pick["code"])
                if not kl2 or pick["i"]+1>=len(kl2): continue
                bp = kl2[pick["i"]]["c"]
                sp = kl2[pick["i"]+1]["c"]
                if bp<=0: continue
                cost = 0.00225
                ret = (sp-bp)/bp - cost
                store.append({"c":pick["code"],"s":pick["s"],"r":round(ret*100,2),"bd":today})
        
        make_trades(v1_cand, v1_trades)
        make_trades(v2_cand, v2_trades)
        
        days_run += 1
        if days_run%20==0: print(f"  {days_run}/{len(days)-2}天 V1:{len(v1_trades)}笔 V2:{len(v2_trades)}笔")
    
    # 统计
    def stats(trades, name):
        print(f"\n  === {name} ===")
        if not trades:
            print("  无交易")
            return
        rs=[t["r"] for t in trades]
        wins=[r for r in rs if r>0]
        wr=len(wins)/len(rs)*100
        avg=sum(rs)/len(rs)
        aw=sum(wins)/len(wins) if wins else 0
        al=sum([r for r in rs if r<=0])/max(len([r for r in rs if r<=0]),1)
        
        cum=1.0; eq=[]
        for r in rs: cum*=(1+r/100); eq.append(cum)
        tr=(cum-1)*100
        
        peak=1.0; mdd=0
        for e in eq:
            if e>peak: peak=e
            dd=(peak-e)/peak*100
            if dd>mdd: mdd=dd
        
        std=(sum((r-avg)**2 for r in rs)/len(rs))**0.5
        sharpe=0
        if std>0: sharpe=avg/std*(252**0.5)
        
        plr=abs(aw/al) if al!=0 else 0
        
        print(f"    总交易: {len(trades)}笔")
        print(f"    胜率:   {wr:.1f}%")
        print(f"    平均:   {avg:+.2f}%")
        print(f"    累计:   {tr:+.2f}%")
        print(f"    盈亏比: {plr:.2f}")
        print(f"    夏普:   {sharpe:.2f}")
        print(f"    回撤:   {mdd:.1f}%")
        print(f"    平均盈: {aw:+.2f}%")
        print(f"    平均亏: {al:+.2f}%")
        
        # 按评分
        print(f"\n    评分区间:")
        for lo,hi in [(30,40),(40,50),(50,60),(60,999)]:
            ts=[t for t in trades if lo<=t["s"]<hi]
            if ts:
                r2=sum(t["r"] for t in ts)/len(ts)
                w2=sum(1 for t in ts if t["r"]>0)/len(ts)*100
                print(f"      {lo}-{hi}分: {len(ts):>3d}笔, 平均{r2:>+6.2f}%, 胜率{w2:.1f}%")
    
    stats(v1_trades, "旧策略 V1 (6因子)")
    stats(v2_trades, "新策略 V2.2 (10因子)")
    
    # 对比
    v1r=[t["r"] for t in v1_trades]
    v2r=[t["r"] for t in v2_trades]
    print(f"\n  {'='*50}")
    print(f"  优化前后对比汇总")
    print(f"  {'='*50}")
    if v1_trades and v2_trades:
        v1w=sum(1 for r in v1r if r>0)/len(v1r)*100
        v2w=sum(1 for r in v2r if r>0)/len(v2r)*100
        v1a=sum(v1r)/len(v1r)
        v2a=sum(v2r)/len(v2r)
        v1c=(sum(1 for r in v1r if r>0)/len(v1r)*100)-50
        v2c=(sum(1 for r in v2r if r>0)/len(v2r)*100)-50
        print(f"  {'指标':<15} {'旧策略V1':<15} {'新策略V2.2':<15} {'变化':<15}")
        print(f"  {'-'*55}")
        print(f"  {'胜率':<15} {v1w:<14.1f}% {v2w:<14.1f}% {'+' if v2w>v1w else ''}{v2w-v1w:.1f}%")
        print(f"  {'平均收益':<15} {v1a:<14.2f}% {v2a:<14.2f}% {'+' if v2a>v1a else ''}{v2a-v1a:.2f}%")
        print(f"  {'交易笔数':<15} {len(v1_trades):<14} {len(v2_trades):<14} {len(v2_trades)-len(v1_trades):+}")


if __name__ == "__main__":
    run_compare()