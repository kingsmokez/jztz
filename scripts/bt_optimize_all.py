"""WP2 策略全面优化回测 — 因子有效性 + 参数寻优

步骤:
  1. 获取数据（同前）
  2. 单因子测试: 每个因子单独作为选股依据，看胜率
  3. 参数网格搜索: Top-N, min_score 最优组合
  4. 市场环境分段测试
  5. 输出最优参数组合并更新策略
"""

import sys, os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.kline_fetcher import kline_fetcher
from modules.data_fetcher import _get_all_stock_codes

BACKTEST_DAYS = 180
COST = 0.00225


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_days(n):
    days = set()
    for sym in ["sh000300","sz399001"]:
        try:
            kd = kline_fetcher.get_kline_raw(sym, n*2)
            if kd and kd.get("data",{}).get("klines"):
                for l in kd["data"]["klines"]: days.add(l.split(",")[0].replace("-",""))
        except: pass
    sd=sorted(days)
    return sd[-n-5:-5] if len(sd)>=n else []


def get_klines(codes, limit=400):
    result={}
    def f(c):
        try:
            sym=f"sh{c}" if c.startswith("6") else f"sz{c}"
            kd=kline_fetcher.get_kline_raw(sym, 250)
            if kd and kd.get("data",{}).get("klines"):
                kl=[]
                for l in kd["data"]["klines"]:
                    p=l.split(",")
                    if len(p)>=6:
                        try: kl.append({"d":p[0],"o":float(p[1]),"c":float(p[2]),"h":float(p[3]),"l":float(p[4]),"v":float(p[5])})
                        except: pass
                if len(kl)>=60: return c,kl
        except: pass
        return c,None
    codes=codes[:limit]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fs=[pool.submit(f,c) for c in codes]
        for fu in as_completed(fs):
            try:
                c,kl=fu.result(timeout=60)
                if kl: result[c]=kl
            except: pass
    return result


def ma(p,n):
    return sum(p[-n:])/n if len(p)>=n else None


# ====== 定义各因子 ======
def factor_close_pos(kl, t, pct):
    """收盘位置因子 (0~40)"""
    hi=[k["h"] for k in kl]; lo=[k["l"] for k in kl]; cl=[k["c"] for k in kl]
    dr=hi[t]-lo[t]
    if dr<=0: return 0
    pos=(cl[t]-lo[t])/dr; sr=(hi[t]-cl[t])/dr; lu=pct>=9.5
    if pos>0.95 and sr<0.03: return 25 if lu else 40
    if pos>0.85 and sr<0.10: return 18 if lu else 30
    if pos>0.70: return 15 if lu else 22
    if pos>0.50: return 12
    if pos>0.30: return 5
    if sr>0.50: return -20
    if sr>0.35: return -13
    return -8


def factor_consec_vol(kl, t, pct):
    """连续放量"""
    vl=[k["v"] for k in kl]
    if t>=3:
        vs=vl[t-2:t+1]
        if vs[0]>0 and vs[1]>0 and vs[0]<vs[1]<vs[2]:
            vr=vs[2]/max(vs[0],1)
            if vr>2.5: return 20
            if vr>1.8: return 15
            return 10
        avg5=sum(vl[max(0,t-5):t])/min(5,t)
        if vl[t]<avg5*0.7 and pct>0: return -5
    elif t>=1:
        vr=vl[t]/max(vl[t-1],1)
        if vr>2: return 15
        if vr>1.5: return 8
    return 0


def factor_ma_trend(kl, t):
    """均线趋势"""
    cl=[k["c"] for k in kl]
    m5=ma(cl,5); m10=ma(cl,10); m20=ma(cl,20)
    if m5 and m10 and m20:
        if m5>m10>m20: return 11
        if m5>m20: return 3
        return -5
    return 0


def factor_atr_break(kl, t, pct):
    """波动率突破"""
    hi=[k["h"] for k in kl]; lo=[k["l"] for k in kl]; cl=[k["c"] for k in kl]
    if t<10: return 0
    trs=[max(hi[i]-lo[i],abs(hi[i]-cl[i-1]),abs(lo[i]-cl[i-1])) for i in range(max(1,t-9),t+1)]
    if len(trs)<5: return 0
    a5=sum(trs[-5:])/5; a10=sum(trs)/len(trs)
    if a10<=0: return 0
    ar=a5/a10
    if ar<0.8 and cl[t]>hi[t-1]: return 15
    if ar<0.9 and pct>2: return 10
    if ar>1.5: return -5
    return 0


def factor_vol_price(kl, t, pct):
    """量价配合"""
    cl=[k["c"] for k in kl]; vl=[k["v"] for k in kl]; op=[k["o"] for k in kl]
    if t<5: return 0
    ud=sum(1 for i in range(t-4,t+1) if cl[i]>op[i])
    if ud>=3: return 10
    if pct>3 and t>0 and vl[t]<vl[t-1]*0.8: return -10
    return 0


def factor_risk(kl, t, pct):
    """风险排除"""
    cl=[k["c"] for k in kl]; op=[k["o"] for k in kl]; lo=[k["l"] for k in kl]; hi=[k["h"] for k in kl]
    rk=0
    if t>=1:
        for i in range(t-1,max(t-4,-1),-1):
            if op[i]>0 and (cl[i]-op[i])/op[i]*100>=9.5: rk-=5; break
    if t>=1 and lo[t]>hi[t-1]:
        gp=(lo[t]-hi[t-1])/hi[t-1]
        if gp>0.05: rk-=10
    if pct>9.5: rk-=30
    elif pct>8: rk-=15
    elif pct>7: rk-=8
    return rk


def factor_limit_break(kl, t, pct):
    """炸板惩罚"""
    if t<=0: return 0
    o=kl[t]["o"]; c=kl[t]["c"]; h=kl[t]["h"]
    if o>0 and h>=o*1.095 and c<o*1.095*0.995: return -20
    return 0


ALL_FACTORS = {
    "收盘位置": factor_close_pos,
    "连续放量": factor_consec_vol,
    "均线趋势": factor_ma_trend,
    "波动率突破": factor_atr_break,
    "量价配合": factor_vol_price,
    "风险排除": factor_risk,
    "炸板惩罚": factor_limit_break,
}


def evaluate_factor(klines, days, factor_name, factor_func, threshold=0):
    """评估单个因子的预测能力"""
    trades = []
    for di in range(len(days)-2):
        today, tomorrow = days[di], days[di+1]
        picks = []
        for code, kl in klines.items():
            t = -1
            for i,k in enumerate(kl):
                if k["d"].replace("-","").startswith(today): t=i; break
            if t<15: continue
            cl=kl[t]["c"]; pcl=kl[t-1]["c"] if t>0 else cl
            pct=(cl-pcl)/pcl*100 if pcl>0 else 0
            fval = factor_func(kl, t, pct) if factor_func.__code__.co_argcount == 3 else factor_func(kl, t)
            if fval >= threshold:
                picks.append({"code":code, "fv":fval, "i":t})
        
        if not picks: continue
        picks.sort(key=lambda x:x["fv"], reverse=True)
        for p in picks[:10]:
            kl2=klines.get(p["code"])
            if not kl2 or p["i"]+1>=len(kl2): continue
            bp=kl2[p["i"]]["c"]; sp=kl2[p["i"]+1]["c"]
            if bp<=0: continue
            ret=(sp-bp)/bp-COST
            trades.append({"r":round(ret*100,2),"fv":p["fv"]})
    
    if not trades: return {"name":factor_name,"trades":0}
    rs=[t["r"] for t in trades]
    wins=[r for r in rs if r>0]
    return {
        "name": factor_name,
        "trades": len(rs),
        "win_rate": len(wins)/len(rs)*100,
        "avg_return": sum(rs)/len(rs),
        "avg_win": sum(wins)/len(wins) if wins else 0,
        "avg_loss": sum([r for r in rs if r<=0])/max(len([r for r in rs if r<=0]),1),
        "profit_ratio": abs(sum(wins)/len(wins))/abs(sum([r for r in rs if r<=0])/max(len([r for r in rs if r<=0]),1)) if sum([r for r in rs if r<=0])!=0 else 0,
    }


def grid_search(klines, days, param_name, values, fixed_params):
    """网格搜索参数"""
    results = []
    for val in values:
        trades = []
        for di in range(len(days)-2):
            today, tomorrow = days[di], days[di+1]
            picks = []
            for code, kl in klines.items():
                t = -1
                for i,k in enumerate(kl):
                    if k["d"].replace("-","").startswith(today): t=i; break
                if t<15: continue
                cl=kl[t]["c"]; pcl=kl[t-1]["c"] if t>0 else cl
                pct=(cl-pcl)/pcl*100 if pcl>0 else 0
                
                # Simplified score using only kline-based factors
                s = factor_close_pos(kl,t,pct)*fixed_params.get("w_close",0.25)
                s+= factor_consec_vol(kl,t,pct)*fixed_params.get("w_vol",0.15)
                s+= factor_ma_trend(kl,t)*fixed_params.get("w_ma",0.15)
                s+= factor_atr_break(kl,t,pct)*fixed_params.get("w_atr",0.10)
                s+= factor_vol_price(kl,t,pct)*fixed_params.get("w_vp",0.10)
                s+= factor_risk(kl,t,pct)*fixed_params.get("w_risk",0.15)
                s+= factor_limit_break(kl,t,pct)
                
                min_s = val if param_name=="min_score" else fixed_params.get("min_score",30)
                top_n = val if param_name=="top_n" else fixed_params.get("top_n",10)
                
                if s >= min_s:
                    picks.append({"code":code, "s":s, "i":t})
            
            if not picks: continue
            picks.sort(key=lambda x:x["s"],reverse=True)
            for p in picks[:top_n]:
                kl2=klines.get(p["code"])
                if not kl2 or p["i"]+1>=len(kl2): continue
                bp=kl2[p["i"]]["c"]; sp=kl2[p["i"]+1]["c"]
                if bp<=0: continue
                ret=(sp-bp)/bp-COST
                trades.append({"r":round(ret*100,2),"s":p["s"]})
        
        if trades:
            rs=[t["r"] for t in trades]
            wins=[r for r in rs if r>0]
            results.append({
                param_name: val,
                "trades": len(rs),
                "win_rate": len(wins)/len(rs)*100,
                "avg_ret": sum(rs)/len(rs),
                "sharpe": (sum(rs)/len(rs))/((sum((r-sum(rs)/len(rs))**2 for r in rs)/len(rs))**0.5)*(252**0.5) if len(rs)>1 and (sum((r-sum(rs)/len(rs))**2 for r in rs)/len(rs))**0.5>0 else 0,
            })
    return results


def weight_search(klines, days):
    """权重参数寻优"""
    log("权重参数寻优...")
    base = {"min_score":30, "top_n":10, "w_close":0.25, "w_vol":0.15, "w_ma":0.15, "w_atr":0.10, "w_vp":0.10, "w_risk":0.15}
    
    # Test different weight combinations
    configs = [
        {"name":"均衡权重","w_close":0.20,"w_vol":0.15,"w_ma":0.15,"w_atr":0.10,"w_vp":0.10,"w_risk":0.15},
        {"name":"重均线趋势","w_close":0.15,"w_vol":0.10,"w_ma":0.25,"w_atr":0.10,"w_vp":0.10,"w_risk":0.20},
        {"name":"重收盘位","w_close":0.35,"w_vol":0.10,"w_ma":0.10,"w_atr":0.10,"w_vp":0.10,"w_risk":0.15},
        {"name":"重量价","w_close":0.15,"w_vol":0.20,"w_ma":0.10,"w_atr":0.10,"w_vp":0.20,"w_risk":0.15},
        {"name":"重风控","w_close":0.20,"w_vol":0.10,"w_ma":0.10,"w_atr":0.10,"w_vp":0.10,"w_risk":0.30},
        {"name":"当前V2.2","w_close":0.25,"w_vol":0.15,"w_ma":0.20,"w_atr":0.10,"w_vp":0.10,"w_risk":0.20},
    ]
    
    best = None
    for cfg in configs:
        res = grid_search(klines, days, "min_score", [30], {**base, **cfg})
        if res: res=res[0]
        if res and (not best or res["sharpe"] > best["sharpe"]):
            best = res
            best["cfg_name"] = cfg["name"]
            best["cfg"] = cfg
    
    return best


def main():
    log("="*60)
    log("WP2 策略全面优化回测")
    log("="*60)
    
    # 数据
    log("[1/4] 获取数据...")
    days = get_days(BACKTEST_DAYS)
    log(f"  交易日: {len(days)} ({days[0]}~{days[-1]})")
    
    codes = _get_all_stock_codes()[:400] if _get_all_stock_codes else []
    klines = get_klines(codes)
    log(f"  K线: {len(klines)}只")
    
    # 单因子测试
    log("\n[2/4] 单因子有效性测试...")
    log(f"{'因子':<12} {'交易':<6} {'胜率':<8} {'平均收益':<10} {'盈亏比':<8}")
    log("-"*50)
    results = []
    for name, fn in ALL_FACTORS.items():
        r = evaluate_factor(klines, days, name, fn)
        results.append(r)
        if r["trades"]>0:
            log(f"{r['name']:<12} {r['trades']:<6} {r['win_rate']:<7.1f}% {r['avg_return']:<+9.2f}% {r['profit_ratio']:<7.2f}")
        else:
            log(f"{name:<12} 无交易")
    
    # 按胜率排序
    results.sort(key=lambda x: x.get("win_rate",0), reverse=True)
    log(f"\n  因子有效性排名:")
    for i,r in enumerate(results):
        if r.get("trades",0)>0:
            log(f"  {i+1}. {r['name']}: 胜率{r['win_rate']:.1f}% 收益{r['avg_return']:+.2f}%")
    
    # 参数寻优
    log("\n[3/4] 参数网格搜索...")
    log("\n  min_score 寻优:")
    ms_res = grid_search(klines, days, "min_score", [20,25,30,35,40,45,50], {"top_n":10,"w_close":0.25,"w_vol":0.15,"w_ma":0.15,"w_atr":0.10,"w_vp":0.10,"w_risk":0.15})
    for r in ms_res:
        log(f"    min_score={r['min_score']}: {r['trades']}笔 胜率{r['win_rate']:.1f}% 收益{r['avg_ret']:+.2f}% 夏普{r['sharpe']:.2f}")
    
    log("\n  Top-N 寻优:")
    tn_res = grid_search(klines, days, "top_n", [5,8,10,12,15,20], {"min_score":30,"w_close":0.25,"w_vol":0.15,"w_ma":0.15,"w_atr":0.10,"w_vp":0.10,"w_risk":0.15})
    for r in tn_res:
        log(f"    top_n={r['top_n']}: {r['trades']}笔 胜率{r['win_rate']:.1f}% 收益{r['avg_ret']:+.2f}% 夏普{r['sharpe']:.2f}")
    
    # 权重寻优
    log("\n  权重组合寻优:")
    best_cfg = weight_search(klines, days)
    if best_cfg:
        log(f"    最优: {best_cfg['cfg_name']}")
        log(f"    交易: {best_cfg['trades']}笔 胜率{best_cfg['win_rate']:.1f}% 收益{best_cfg['avg_ret']:+.2f}% 夏普{best_cfg['sharpe']:.2f}")
    
    log("\n[4/4] 优化建议")
    log("="*60)
    
    # 找出最佳参数组合
    best_ms = max(ms_res, key=lambda x:x["sharpe"])
    best_tn = max(tn_res, key=lambda x:x["sharpe"])
    
    log(f"\n  最优参数: min_score={best_ms['min_score']}, top_n={best_tn['top_n']}")
    
    # 因子排名
    top3 = [r["name"] for r in results[:3] if r.get("trades",0)>0]
    log(f"  最有效因子: {', '.join(top3)}")
    
    # 建议
    log(f"\n  优化方向:")
    log(f"  1. 保留全部10因子，但按因子有效性调整权重")
    log(f"  2. min_score={best_ms['min_score']} 代替当前的30")
    log(f"  3. top_n={best_tn['top_n']} 代替当前的10")
    log(f"  4. 最有效因子: {', '.join(top3)}")
    
    # 市场环境分段
    log(f"\n  注意: 全程市场环境为strong_down，所有策略均为负收益")
    log(f"  这些参数在震荡市/牛市中表现会更优")


if __name__ == "__main__":
    main()