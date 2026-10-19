/* Synthetic grid with known missing/partial observations; never calls FPL. */
module.exports=function visualFixture(){
  const a=Array.from({length:13},(_,i)=>101+i),b=Array.from({length:17},(_,i)=>201+i);
  const pairs=a.flatMap((left,i)=>b.map((right,j)=>{
    const average=(i*17+j)%101,partial=left===101&&right===202,missing=left===101&&right===203;
    return {a:left,b:right,a_name:`Checked team ${left}`,b_name:`Reference team ${right}`,
      average:missing?null:average,latest:partial||missing?null:100-average,
      compared_weeks:missing?0:partial?2:3,requested_weeks:3,complete:!partial&&!missing,
      meets_threshold:partial||missing?null:average>=80,weeks_above:average>=80?3:0,
      weeks_above_percent:missing?null:average>=80?100:0,longest_streak:average>=80?3:0,
      status:partial||missing?'Insufficient coverage':average>=80?'Threshold met':'Below threshold'};
  }));
  // An unsafe public name must remain inert text in every chart and selector.
  pairs.find(p=>p.a===101&&p.b===201).a_name='<img src=x onerror="window.visualInjection=true">';
  return {ready:true,season:'2026-27',settings:{group_a:a,group_b:b,start_gw:1,end_gw:3,metric:'squad',threshold:80,min_matching_week_percent:0},
    pairs,matched_pairs:pairs.filter(p=>p.meets_threshold===true).length,incomplete_pairs:2,self_pairs_skipped:0,
    warnings:[],source_checked_at:'2026-10-19T00:00:00Z'};
};
