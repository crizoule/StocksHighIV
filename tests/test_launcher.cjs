const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../templates/launcher.html'),'utf8');
const script=html.match(/<script>([\s\S]*)<\/script>/)[1].replace(/poll\(\);\s*$/, '');
function app(){const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,{removeAttribute(k){delete this[k];},scrollHeight:0,clientHeight:0,scrollTop:0});return elements.get(id);};const scope={document:{getElementById:get},AbortSignal,setTimeout(){}};vm.createContext(scope);vm.runInContext(script,scope);return {get,render:scope.render};}
const state={token:'token',status:'running',phase:'scan',ready:true,total:100,completed:25,rate:50,eta:90,elapsed:30,last_activity_seconds:2,logs:['Checking TEST'],with_iv:20,errors:2,reused:0,has_dashboard:true,activity:'Downloading TEST'};
test('live scan shows measured pace, counts, ETA and keeps saved report accessible',()=>{const a=app();a.render(state);assert.equal(a.get('bar').value,25);assert.match(a.get('counts').textContent,/25 \/ 100/);assert.equal(a.get('rate').textContent,'50.0');assert.equal(a.get('eta').textContent,'1m 30s');assert.equal(a.get('refresh').disabled,true);assert.equal(a.get('dashboard').hidden,false);assert.match(a.get('quality').textContent,/2 failed requests/);});
test('enrichment never invents a percentage or ETA',()=>{const a=app();a.render({...state,phase:'enrich',total:null,completed:12,eta:null});assert.equal(a.get('bar').value,undefined);assert.equal(a.get('eta').textContent,'—');assert.match(a.get('counts').textContent,/12 companies enriched/);});
test('failed first setup offers retry and no unavailable dashboard',()=>{const a=app();a.render({...state,status:'error',phase:'setup',ready:false,has_dashboard:false});assert.equal(a.get('setup').hidden,false);assert.equal(a.get('refresh').hidden,true);assert.equal(a.get('dashboard').hidden,true);assert.equal(a.get('log-details').open,true);});
const controlsScript=fs.readFileSync(path.join(__dirname,'../templates/app-controls.js'),'utf8');
test('saved dashboard remains visible and completion notifies once',async()=>{
  const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,{dataset:{reportStamp:'100'},hidden:true,replaceChildren(){},appendChild(){}});return elements.get(id);};
  const store=new Map(),notices=[];let timer;
  let status={settings:{mode:'auto',time:'11:00'},token:'t',identity:'app',status:'running',activity:'Checking stocks',has_dashboard:true,dashboard_saved_at:100};
  function Notice(title,options){notices.push({title,options});}Notice.permission='granted';
  const window={Notification:Notice,location:{href:'/'},dispatchEvent(){}};
  const scope={document:{getElementById:get},CustomEvent:function(){},window,Notification:Notice,AbortSignal,localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},fetch:async()=>({ok:true,json:async()=>status}),setTimeout:fn=>{timer=fn;}};
  vm.createContext(scope);vm.runInContext(controlsScript,scope);await new Promise(setImmediate);
  assert.equal(get('local-complete').hidden,true);assert.equal(get('local-saved').hidden,false);
  status={...status,status:'done',dashboard_saved_at:200,completion_id:'done1'};
  await timer();assert.equal(get('local-complete').hidden,false);assert.equal(notices.length,1);assert.equal(window.location.href,'/');
  await timer();assert.equal(notices.length,1);
});
