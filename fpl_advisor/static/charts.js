/* Small, accessible SVG charts. No third-party scripts or network dependencies. */
function svgChart({labels=[], series=[], title='Player chart', unit='', height=230}) {
  const clean=series.filter(s=>s.values.some(v=>v!=null&&Number.isFinite(Number(v))));
  if(!labels.length||!clean.length) return '<p class="empty">No recorded observations for this view yet.</p>';
  const W=540,H=height,L=48,R=15,T=20,B=44,plotW=W-L-R,plotH=H-T-B;
  const numbers=clean.flatMap(s=>s.values.filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number));
  let low=numbers.reduce((a,b)=>Math.min(a,b),0),high=numbers.reduce((a,b)=>Math.max(a,b),1);
  const pad=(high-low)*.12; if(low<0) low-=pad; high+=pad;
  const x=i=>L+(labels.length===1?plotW/2:i*plotW/(labels.length-1));
  const y=v=>T+(high-v)*plotH/(high-low),pieces=[];
  for(let tick=0;tick<5;tick++) {
    const value=low+(high-low)*tick/4, yy=y(value);
    pieces.push(`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#e9e5ef"/><text x="${L-8}" y="${yy+4}" text-anchor="end" class="axis-label">${esc(Math.abs(value)>=10?Math.round(value):value.toFixed(1))}</text>`);
  }
  labels.forEach((label,i)=>{
    if(labels.length<=15||i%Math.ceil(labels.length/12)===0||i===labels.length-1)
      pieces.push(`<text x="${x(i)}" y="${H-20}" text-anchor="middle" class="axis-label">${esc(String(label).length>6?String(label).slice(0,5)+"…":label)}<title>${esc(label)}</title></text>`);
  });
  clean.forEach((s,group)=>{
    let segment=[];
    const path=()=>{if(segment.length)pieces.push(`<polyline points="${segment.join(' ')}" fill="none" stroke="${s.color}" stroke-width="2.5" ${s.dashed?'stroke-dasharray="6 5"':''}/>`);segment=[];};
    s.values.forEach((raw,i)=>{
      if(raw==null||!Number.isFinite(Number(raw))){path();return;}
      const value=Number(raw),xx=x(i),yy=y(value),tip=`${s.name}: ${labels[i]}, ${value.toFixed(2)} ${unit}`;
      if(s.type==='bar') {
        const width=Math.min(28,plotW/Math.max(labels.length,2)*.55/clean.length);
        const start=xx+(group-(clean.length-1)/2)*width-width/2;
        pieces.push(`<rect x="${start}" y="${Math.min(yy,y(0))}" width="${width}" height="${Math.max(1,Math.abs(yy-y(0)))}" rx="2" fill="${s.color}" opacity=".8" tabindex="0" role="img" aria-label="${esc(tip)}"><title>${esc(tip)}</title></rect>`);
      } else {
        segment.push(`${xx},${yy}`);
        pieces.push(`<circle cx="${xx}" cy="${yy}" r="3.5" fill="${s.color}" tabindex="0" role="img" aria-label="${esc(tip)}"><title>${esc(tip)}</title></circle>`);
      }
    });path();
  });
  const description=clean.map(s=>s.name+': '+s.values.map((v,i)=>v==null?'':labels[i]+' '+Number(v).toFixed(2)).filter(Boolean).join(', ')).join('; ');
  return `<div class="svg-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}" preserveAspectRatio="xMidYMid meet"><title>${esc(title)}</title><desc>${esc(description)}</desc>${pieces.join('')}</svg><div class="chart-legend">${clean.map(s=>`<span><i style="background:${s.color};${s.dashed?'border-top:2px dashed white;':''}"></i>${esc(s.name)}</span>`).join('')}</div></div>`;
}
