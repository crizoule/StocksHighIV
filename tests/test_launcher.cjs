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
test('a pending update says it is waiting for the download instead of staying silent',()=>{const a=app();a.render(state);assert.equal(a.get('update-waiting').hidden,true);
  a.render({...state,update_waiting:true});assert.equal(a.get('update-waiting').hidden,false);assert.match(a.get('update-waiting').textContent,/installs as soon as this download finishes/);
  a.render({...state,status:'done',update_waiting:true});assert.match(a.get('update-waiting').textContent,/ready to install/);});

test('failed first setup offers retry and no unavailable dashboard',()=>{const a=app();a.render({...state,status:'error',phase:'setup',ready:false,has_dashboard:false});assert.equal(a.get('setup').hidden,false);assert.equal(a.get('refresh').hidden,true);assert.equal(a.get('dashboard').hidden,true);assert.equal(a.get('log-details').open,true);});
const controlsScript=fs.readFileSync(path.join(__dirname,'../templates/app-controls.js'),'utf8');
test('saved dashboard remains visible and completion notifies once',async()=>{
  const elements=new Map();const get=id=>{if(!elements.has(id))elements.set(id,{dataset:{reportStamp:'100'},hidden:true,replaceChildren(){},appendChild(){}});return elements.get(id);};
  const store=new Map(),notices=[];let timer;
  let status={settings:{mode:'auto',time:'11:00'},token:'t',identity:'app',status:'running',phase:'scan',total:100,completed:25,eta:90,activity:'Checking stocks',has_dashboard:true,dashboard_saved_at:100};
  function Notice(title,options){notices.push({title,options});}Notice.permission='granted';
  const window={Notification:Notice,location:{href:'/'},dispatchEvent(){}};
  const scope={document:{getElementById:get,addEventListener(){}},CustomEvent:function(){},window,Notification:Notice,AbortSignal,localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},fetch:async()=>({ok:true,json:async()=>status}),setTimeout:fn=>{timer=fn;}};
  vm.createContext(scope);vm.runInContext(controlsScript,scope);await new Promise(setImmediate);
  assert.equal(get('local-complete').hidden,true);assert.equal(get('local-saved').hidden,false);
  assert.match(get('local-progress').textContent,/IV scan 25%/);
  assert.match(get('local-progress').textContent,/~2m remaining/);
  status={...status,phase:'enrich',eta:null}; await timer();
  assert.doesNotMatch(get('local-progress').textContent,/remaining|IV scan/);
  status={...status,status:'done',dashboard_saved_at:200,completion_id:'done1'};
  await timer();assert.equal(get('local-complete').hidden,false);assert.equal(notices.length,1);assert.equal(window.location.href,'/');
  await timer();assert.equal(notices.length,1);
});

test('watchlist and schedule sit in a menu that opens from the bar and closes on Escape or a click elsewhere',()=>{
  const elements=new Map(),handlers={};
  const get=id=>{if(!elements.has(id))elements.set(id,{id,dataset:{reportStamp:'0'},hidden:true,attributes:{},value:'',
    setAttribute(k,v){this.attributes[k]=v;},contains(node){return node===this||node?.parent===this;},focus(){focused=this.id;},replaceChildren(){},appendChild(){}});return elements.get(id);};
  let focused=null;
  const scope={document:{getElementById:get,addEventListener:(type,fn)=>{handlers[type]=fn;}},CustomEvent:function(){},window:{dispatchEvent(){}},
    AbortSignal,localStorage:{getItem(){return null;},setItem(){}},fetch:async()=>({ok:false}),setTimeout(){}};
  vm.createContext(scope);vm.runInContext(controlsScript,scope);
  const menu=get('local-menu'),toggle=get('local-menu-toggle');
  assert.equal(menu.hidden,true);
  toggle.onclick();assert.equal(menu.hidden,false);assert.equal(toggle.attributes['aria-expanded'],'true');
  handlers.click({target:{parent:menu}});assert.equal(menu.hidden,false);  // using the watchlist form keeps it open
  handlers.click({target:{}});assert.equal(menu.hidden,true);assert.equal(toggle.attributes['aria-expanded'],'false');
  toggle.onclick();handlers.keydown({key:'Escape'});assert.equal(menu.hidden,true);assert.equal(focused,'local-menu-toggle');
  toggle.onclick();toggle.onclick();assert.equal(menu.hidden,true);  // the button toggles
});
