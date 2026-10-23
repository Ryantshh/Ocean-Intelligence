const {test}=require('node:test');
const assert=require('node:assert/strict');
const {compare,total,build,shiftDay}=require('../ai_platform/dashboard/manager-report.js');
const range={since:'2025-12-17T14:30:00',until:'2026-01-15T14:30:00'};
function series() {return Array.from({length:30},(_,i)=>({day:shiftDay('2025-12-17',i),count:10,up:6,down:4,regions:[{region:'A',count:8,up:5,down:3},{region:'B',count:8,up:5,down:3}]}));}
test('14-day comparison excludes partial boundaries and crosses year correctly',()=>{
 const rows=series(); rows[0].count=999; rows.at(-1).count=999;
 const w=compare(rows,range,14); assert.equal(w.start,'2026-01-01'); assert.equal(w.previousStart,'2025-12-18'); assert.equal(w.current.at(-1).day,'2026-01-14'); assert.equal(total(w.current),140); assert.equal(total(w.previous),140);
});
test('regional overlap does not inflate global totals; inputs are immutable',()=>{
 const rows=series(),before=JSON.stringify(rows);
 assert.equal(total(compare(rows,range,7).current),70);
 assert.equal(total(compare(rows,range,7,'A').current),56);
 assert.equal(total(compare(rows,range,7,'Missing').current),0);
 assert.equal(JSON.stringify(rows),before);
});
test('missing days, unsupported periods and incomplete coverage fail explicitly',()=>{
 assert.throws(()=>compare(series().slice(10),range,14),/Missing daily coverage/);
 assert.throws(()=>compare(series(),range,8),/unavailable/);
 assert.throws(()=>compare(series(),{...range,since:'2026-01-01T14:30:00'},14),/Insufficient/);
 const rows=series(); delete rows[20].regions;
 assert.throws(()=>compare(rows,range,7,'A'),/breakdown/);
});
test('null and negative counts cannot silently become zero',()=>{
 const rows=series(); rows[22].count=null;
 assert.throws(()=>compare(rows,range,7),/invalid count/);
 rows[22].count=-1; assert.throws(()=>compare(rows,range,7),/invalid count/);
});
test('vessel and order comparisons use independent simulation clocks',()=>{
 const orders=series().map(r=>({...r,day:shiftDay(r.day,-1)}));
 const s={counts:{OPEN:1,'ON SUBS':2,FIXED:3,'LIKELY FIXED':4},regions:[],daily:{range,orders_range:{since:'2025-12-16T14:30:00',until:'2026-01-14T14:30:00'},new_vessels:series(),status_changes:series(),new_orders:orders}};
 const m=build(s,7,''); assert.equal(m.vessels.end,'2026-01-15'); assert.equal(m.orders.end,'2026-01-14'); assert.equal(total(m.moves.current,'up'),42);
 delete s.counts.OPEN; assert.throws(()=>build(s,7,''),/invalid count/);
});
test('report refuses fleet totals from a different region and computes selected-period regional orders',()=>{
 const rows=series(); const s={region:'A',counts:{OPEN:4,'ON SUBS':1,FIXED:0,'LIKELY FIXED':2},regions:[{region:'A',supply:4,demand:200},{region:'B',supply:8,demand:100}],daily:{range,orders_range:range,new_vessels:rows,status_changes:rows,new_orders:rows}};
 assert.throws(()=>build(s,7,'B'),/do not match/);
 const seven=build(s,7,'A'), fourteen=build(s,14,'A');
 assert.equal(seven.regions.length,1);assert.equal(seven.regions[0].periodOrders,56);assert.equal(fourteen.regions[0].periodOrders,112);
 assert.equal(seven.regions[0].demand,200);assert.equal(seven.regions[0].previousOrders,56);
});
