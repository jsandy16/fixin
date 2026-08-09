"""Generate an interactive HTML dashboard FROM a completed backtest run.
Nothing is hard-coded: every number, chart and table is derived from the
equity Series and trades DataFrame handed in."""
import json, numpy as np, pandas as pd
from . import report

def _series(E, n=430):
    step = max(1, len(E)//n)
    return [str(x.date()) for x in E.index[::step]], [round(float(v)) for v in E.values[::step]]

def build_payload(E, T, capital, bench=None, meta=None):
    k = report.metrics(E, T, capital)
    x, y = _series(E)
    dd = (E - E.cummax())/E.cummax()
    step = max(1, len(E)//430)
    payload = dict(
        meta = meta or {},
        k = {a: (None if (isinstance(b, float) and not np.isfinite(b)) else
                 (round(float(b), 4) if isinstance(b, (int, float, np.floating)) else b))
             for a, b in k.items()},
        eq = dict(x=x, y=y, dd=[round(float(v)*100, 2) for v in dd.values[::step]]),
    )
    if bench is not None:
        b = bench.reindex(E.index).ffill().bfill()
        payload['eq']['bench'] = [round(capital*float(v)/float(b.iloc[0])) for v in b.values[::step]]
        payload['k']['bench_return'] = round(float(b.iloc[-1]/b.iloc[0]-1)*100, 2)
    # monthly
    mt = report.monthly_table(E)
    payload['monthly'] = dict(years=[int(i) for i in mt.index], cols=list(mt.columns),
        data=[[None if pd.isna(v) else float(v) for v in row] for row in mt.values])
    # exit types
    ex = T.groupby('typ').agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda s:(s>0).mean()*100), avg=('ret','mean'), hold=('hold','mean'))
    payload['exits'] = [[i]+[round(float(v),2) for v in r] for i, r in ex.iterrows()]
    # per symbol
    sy = T.groupby('symbol').agg(n=('pnl','size'), tot=('pnl','sum'),
        win=('ret', lambda s:(s>0).mean()*100), avg=('ret','mean'))
    sy = sy.sort_values('tot', ascending=False)
    payload['symbols'] = [[i, int(r.n), round(float(r.tot)), round(float(r.win),1),
                           round(float(r.avg),2)] for i, r in sy.iterrows()]
    # yearly
    ye = E.resample('YE').last()
    yr = [(str(ye.index[0].year), float(ye.iloc[0]/capital-1))] + \
         [(str(i.year), float(v)) for i, v in ye.pct_change().dropna().items()]
    payload['yearly'] = [[a, round(b*100,1)] for a, b in yr]
    # trades (capped for page weight; full set is in trades.csv)
    tt = T.sort_values('entry_dt').tail(1500)
    payload['trades'] = [[r.symbol, str(pd.Timestamp(r.entry_dt).date()),
        str(pd.Timestamp(r.exit_dt).date()), r.typ, round(float(r.entry_px),2),
        round(float(r.exit_px),2), int(r.qty), round(float(r.pnl)),
        round(float(r.ret),2), int(r.hold)] for r in tt.itertuples()]
    payload['trades_shown'] = len(tt); payload['trades_total'] = len(T)
    return payload

def render(payload, path=None):
    html = _TEMPLATE.replace('__PAYLOAD__', json.dumps(payload))
    if path: open(path, 'w').write(html)
    return html

_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MR-v5 Backtest</title>
<style>
:root{--bg:#131722;--panel:#1B2230;--p2:#161C28;--line:#2A3242;--txt:#D1D4DC;--dim:#787B86;
--up:#26A69A;--dn:#EF5350;--acc:#2962FF;--wn:#F0A93B;--m:ui-monospace,"SF Mono",Menlo,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:var(--m);font-size:12px;line-height:1.55}
.wrap{max-width:1160px;margin:0 auto;padding:0 16px 70px}
header{padding:24px 0 14px;border-bottom:1px solid var(--line)}
.k{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
h1{font-size:20px;font-weight:600;margin:6px 0 3px}
.sub{color:var(--dim);font-size:11px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin:16px 0 0}
.hero div{background:var(--panel);padding:12px 14px}
.hero .l{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.hero .v{font-size:19px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.hero .s{font-size:10px;color:var(--dim);margin-top:2px}
nav{display:flex;border-bottom:1px solid var(--line);margin-top:16px;overflow-x:auto;position:sticky;top:0;background:var(--bg);z-index:9}
nav button{font-family:var(--m);font-size:11.5px;background:none;border:0;color:var(--dim);padding:11px 14px;cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap}
nav button:hover{color:var(--txt)}
nav button[aria-selected=true]{color:var(--txt);border-bottom-color:var(--acc)}
section{display:none;padding-top:18px}section.on{display:block}
h2{font-size:13px;font-weight:600;margin:20px 0 8px}h2:first-child{margin-top:0}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{text-align:right;font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--dim);padding:7px 8px;border-bottom:1px solid var(--line);background:var(--p2);position:sticky;top:39px;cursor:pointer}
th:first-child{text-align:left}
td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right}
td:first-child{text-align:left}
tbody tr:hover{background:var(--p2)}
.up{color:var(--up)}.dn{color:var(--dn)}.dim{color:var(--dim)}
figure{background:var(--panel);border:1px solid var(--line);padding:13px;margin:10px 0}
figcaption{color:var(--dim);font-size:10.5px;margin-top:8px}
svg{display:block;width:100%;height:auto}
.heat td{font-size:10.5px;padding:4px 5px;text-align:center}
.heat td:first-child{text-align:right;font-weight:600}
input{background:var(--p2);border:1px solid var(--line);color:var(--txt);font-family:var(--m);
font-size:11.5px;padding:6px 9px;margin:6px 0 10px;width:220px}
.cfg{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;background:var(--line);border:1px solid var(--line)}
.cfg div{background:var(--panel);padding:8px 12px;font-size:11px}
.cfg span{color:var(--dim)}
</style></head><body><div class="wrap">
<header><div class="k" id="kick"></div><h1 id="ttl">Backtest results</h1>
<div class="sub" id="sub"></div><div class="hero" id="hero"></div></header>
<nav role="tablist">
<button role="tab" aria-selected="true" data-t="ov">Overview</button>
<button role="tab" aria-selected="false" data-t="perf">Performance</button>
<button role="tab" aria-selected="false" data-t="mon">Monthly</button>
<button role="tab" aria-selected="false" data-t="ex">Exits</button>
<button role="tab" aria-selected="false" data-t="sym">Symbols</button>
<button role="tab" aria-selected="false" data-t="tr">Trades</button>
<button role="tab" aria-selected="false" data-t="cfg">Config</button>
</nav>
<section id="ov" class="on">
<figure><div id="ceq"></div><figcaption id="capeq"></figcaption></figure>
<figure><div id="cdd"></div><figcaption>Drawdown, daily mark-to-market.</figcaption></figure>
<h2>Yearly returns</h2><table id="tyr"></table></section>
<section id="perf"><h2>Performance summary</h2><table id="tperf"></table></section>
<section id="mon"><h2>Monthly returns %</h2><table class="heat" id="tmon"></table></section>
<section id="ex"><h2>By exit type</h2><table id="tex"></table></section>
<section id="sym"><h2>By symbol</h2><input id="fsym" placeholder="filter symbol..."><table id="tsym"></table></section>
<section id="tr"><h2>Trades <span class="dim" id="trn"></span></h2>
<input id="ftr" placeholder="filter symbol..."><table id="ttr"></table></section>
<section id="cfg"><h2>Run configuration</h2><div class="cfg" id="tcfg"></div>
<p style="color:var(--dim);margin-top:14px;max-width:80ch">Generated from this run's own output. Re-run the backtest with different parameters and this page regenerates from the new data.</p></section>
</div>
<script>
const D=__PAYLOAD__, K=D.k, M=D.meta;
const R=n=>(n<0?'-':'')+'Rs '+Math.abs(Math.round(n)).toLocaleString('en-IN');
const P=(n,d=2)=>n==null?'-':(n>=0?'':'-')+Math.abs(n).toFixed(d)+'%';
const C=n=>n>=0?'up':'dn';
document.getElementById('kick').textContent='Portfolio backtest · generated '+(M.generated||'');
document.getElementById('ttl').textContent=M.title||'MR-v5 backtest results';
document.getElementById('sub').textContent=
 `${M.universe||'?'} symbols · ${M.start||''} to ${M.end||''} · Rs ${(M.capital||0).toLocaleString('en-IN')} · ${M.slots||'?'} slots · ${((M.cost||0)*10000).toFixed(0)}bps`;
const hero=[['Net profit',R(K.net),P(K.net_pct,1),C(K.net)],
 ['CAGR',P(K.cagr),'monthly '+P(K.monthly),C(K.cagr)],
 ['Max drawdown',P(K.max_dd),'Calmar '+(K.calmar??0).toFixed(2),'dn'],
 ['Sharpe / Sortino',(K.sharpe??0).toFixed(2)+' / '+(K.sortino??0).toFixed(2),'',''],
 ['Trades',K.trades,(K.per_year??0).toFixed(0)+'/yr',''],
 ['Win rate',P(K.win_rate,1),'PF '+(K.profit_factor??0).toFixed(2),'']];
if(K.bench_return!=null)hero.push(['Buy & hold',P(K.bench_return,1),'benchmark','dim']);
document.getElementById('hero').innerHTML=hero.map(([l,v,s,c])=>
 `<div><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`).join('');
const btns=[...document.querySelectorAll('nav button')];
btns.forEach(b=>b.onclick=()=>{btns.forEach(x=>x.setAttribute('aria-selected',x===b));
document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===b.dataset.t));});
document.getElementById('tperf').innerHTML='<thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>'+
[['Net profit',R(K.net)+' ('+P(K.net_pct,1)+')'],['Final equity',R(K.final)],
['Gross profit',R(K.gross_profit)],['Gross loss',R(K.gross_loss)],['Commission paid',R(K.commission)],
['CAGR',P(K.cagr)],['Avg monthly',P(K.monthly)],['Max drawdown',P(K.max_dd)],
['Sharpe',(K.sharpe??0).toFixed(3)],['Sortino',(K.sortino??0).toFixed(3)],['Calmar',(K.calmar??0).toFixed(3)],
['Profit factor',(K.profit_factor??0).toFixed(3)],['Total trades',K.trades],['Trades / year',(K.per_year??0).toFixed(0)],
['Win rate',P(K.win_rate,2)],['Avg return / trade',P(K.avg_ret)],['Avg win',R(K.avg_win)],['Avg loss',R(K.avg_loss)],
['Win/loss ratio',(K.ratio??0).toFixed(3)],['Largest win',R(K.largest_win)],['Largest loss',R(K.largest_loss)],
['Avg hold (days)',(K.avg_hold??0).toFixed(1)],['Max consec wins',K.max_cons_win],['Max consec losses',K.max_cons_loss],
['Positive months',P(K.pos_months,0)],['Best month',P(K.best_month)],['Worst month',P(K.worst_month)]]
.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('')+'</tbody>';
document.getElementById('tyr').innerHTML='<thead><tr><th>Year</th><th>Return</th></tr></thead><tbody>'+
D.yearly.map(r=>`<tr><td>${r[0]}</td><td class="${C(r[1])}">${r[1]}%</td></tr>`).join('')+'</tbody>';
const MM=D.monthly,flat=MM.data.flat().filter(v=>v!=null),mx=Math.max(...flat.map(Math.abs))||1;
document.getElementById('tmon').innerHTML='<thead><tr><th>Year</th>'+MM.cols.map(c=>`<th style="text-align:center">${c}</th>`).join('')+'</tr></thead><tbody>'+
MM.data.map((row,i)=>`<tr><td>${MM.years[i]}</td>`+row.map((v,j)=>{
 if(v==null)return '<td class="dim">·</td>';
 if(j===row.length-1)return `<td class="${C(v)}">${v.toFixed(1)}</td>`;
 const a=Math.min(Math.abs(v)/mx,1)*.6;
 return `<td style="background:${v>0?`rgba(38,166,154,${a})`:`rgba(239,83,80,${a})`}">${v.toFixed(1)}</td>`}).join('')+'</tr>').join('')+'</tbody>';
document.getElementById('tex').innerHTML='<thead><tr><th>Exit</th><th>Trades</th><th>Net P&L</th><th>Win %</th><th>Avg %</th><th>Avg days</th></tr></thead><tbody>'+
D.exits.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td class="${C(r[2])}">${R(r[2])}</td><td>${r[3]}%</td><td class="${C(r[4])}">${r[4]}%</td><td>${r[5]}</td></tr>`).join('')+'</tbody>';
function drawSym(f){const rows=D.symbols.filter(r=>!f||r[0].includes(f));
document.getElementById('tsym').innerHTML='<thead><tr><th>Symbol</th><th>Trades</th><th>Net P&L</th><th>Win %</th><th>Avg %</th></tr></thead><tbody>'+
rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td class="${C(r[2])}">${R(r[2])}</td><td>${r[3]}%</td><td class="${C(r[4])}">${r[4]}%</td></tr>`).join('')+'</tbody>';}
drawSym('');document.getElementById('fsym').oninput=e=>drawSym(e.target.value.trim().toLowerCase());
document.getElementById('trn').textContent=`(showing ${D.trades_shown} of ${D.trades_total} — full set in trades.csv)`;
function drawTr(f){const rows=D.trades.filter(r=>!f||r[0].includes(f));
document.getElementById('ttr').innerHTML='<thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>Type</th><th>Entry Rs</th><th>Exit Rs</th><th>Qty</th><th>P&L</th><th>Ret %</th><th>Days</th></tr></thead><tbody>'+
rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td><td>${r[5]}</td><td>${r[6]}</td><td class="${C(r[7])}">${R(r[7])}</td><td class="${C(r[8])}">${r[8]}%</td><td>${r[9]}</td></tr>`).join('')+'</tbody>';}
drawTr('');document.getElementById('ftr').oninput=e=>drawTr(e.target.value.trim().toLowerCase());
document.getElementById('tcfg').innerHTML=Object.entries(M).map(([a,b])=>`<div><span>${a}</span><br>${b}</div>`).join('');
function line(el,H,series,fmt,logs){const W=1040,Pd={t:12,r:56,b:22,l:64},tr=v=>logs?Math.log(Math.max(v,1)):v;
const all=series.flatMap(s=>s.y).map(tr);let lo=Math.min(...all),hi=Math.max(...all);
const n=series[0].y.length,X=i=>Pd.l+i/(n-1)*(W-Pd.l-Pd.r),Y=v=>Pd.t+(1-(tr(v)-lo)/(hi-lo||1))*(H-Pd.t-Pd.b);
let g='';for(let k=0;k<=4;k++){const tv=lo+(hi-lo)*k/4,v=logs?Math.exp(tv):tv;
g+=`<line x1="${Pd.l}" y1="${Y(v)}" x2="${W-Pd.r}" y2="${Y(v)}" stroke="#2A3242"/><text x="${Pd.l-8}" y="${Y(v)+4}" text-anchor="end" font-size="10" fill="#787B86">${fmt(v)}</text>`}
for(let i=0;i<n;i+=Math.max(1,Math.floor(n/9)))g+=`<text x="${X(i)}" y="${H-6}" text-anchor="middle" font-size="10" fill="#787B86">${D.eq.x[i].slice(0,4)}</text>`;
series.forEach(s=>{const d=s.y.map((v,i)=>(i?'L':'M')+X(i).toFixed(1)+' '+Y(v).toFixed(1)).join(' ');
if(s.f!==undefined)g+=`<path d="${d} L${X(n-1)} ${Y(s.f)} L${X(0)} ${Y(s.f)} Z" fill="rgba(239,83,80,.15)"/>`;
g+=`<path d="${d}" fill="none" stroke="${s.c}" stroke-width="${s.w||1.8}" ${s.d?'stroke-dasharray="4 3"':''}/>`;
g+=`<text x="${W-Pd.r+5}" y="${Y(s.y[n-1])+4}" font-size="10" fill="${s.c}">${s.l}</text>`});
el.innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}</svg>`}
const ser=[{y:D.eq.y,c:'#26A69A',l:'strategy'}];
if(D.eq.bench)ser.unshift({y:D.eq.bench,c:'#787B86',l:'B&H',d:1,w:1.4});
line(document.getElementById('ceq'),300,ser,v=>(v/1e6).toFixed(1)+'m',true);
document.getElementById('capeq').textContent='Equity, log scale.'+(D.eq.bench?' Grey = buy & hold benchmark.':'');
line(document.getElementById('cdd'),165,[{y:D.eq.dd,c:'#EF5350',l:'DD',f:0}],v=>v.toFixed(0)+'%',false);
</script></body></html>"""
