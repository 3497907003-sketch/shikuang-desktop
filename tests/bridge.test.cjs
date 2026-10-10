const assert=require('node:assert/strict'),test=require('node:test'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../desktop/bridge.js'),'utf8').replace('__DESKTOP_METADATA__',JSON.stringify({origin:'http://127.0.0.1:8000',version:'test'}));
function environment({registrations=[],controller=null,pending=false}={}){
  let clock=0,sequence=0;const timers=new Map(),nodes=new Map(),calls={unregister:[],deleted:[],reload:0,prepare:0,reference:[]};
  function element(id=''){return {id,textContent:'',disabled:false,attrs:new Set(),children:[],listeners:[],setAttribute(k){this.attrs.add(k);},removeAttribute(k){this.attrs.delete(k);},hasAttribute(k){return this.attrs.has(k);},appendChild(e){this.children.push(e);if(e.id)nodes.set(e.id,e);},remove(){nodes.delete(this.id);},addEventListener(name,handler,capture){this.listeners.push({name,handler,capture});}};}
  const html=element(),body=element();Object.defineProperty(body,'innerHTML',{set(v){body.markup=v;nodes.set('desktop-error',element('desktop-error'));nodes.set('desktop-retry',element('desktop-retry'));}});
  for(const id of ['offlineBtn','offlineInfo','offlineProgress'])nodes.set(id,element(id));
  const document={documentElement:html,head:element(),body,createElement:()=>element(),getElementById:id=>nodes.get(id)||null};
  const root={document,Promise,URL,AbortController,Worker:function(){},WebAssembly:{},crypto:{subtle:{}},Image:function(){},__shikuangAppReady:true,
    navigator:{serviceWorker:{controller,getRegistrations:()=>pending?new Promise(()=>{}):Promise.resolve(registrations.map(scope=>({scope,unregister(){calls.unregister.push(scope);return Promise.resolve(true);}})))}},
    caches:{keys:()=>Promise.resolve(['shikuang-static-old','other-product']),delete:name=>{calls.deleted.push(name);return Promise.resolve(true);}},
    location:{reload:()=>calls.reload++},setTimeout(fn,delay){const id=++sequence;timers.set(id,{fn,at:clock+delay});return id;},clearTimeout:id=>timers.delete(id),
    MineralEngine:{prepare:()=>{calls.prepare++;return Promise.resolve({});}},MineralReference:{load:options=>{calls.reference.push(options);return Promise.resolve({});}}};
  root.Image.prototype.decode=function(){};
  vm.runInNewContext(source,{window:root,Promise,URL});
  const flush=async()=>{for(let n=0;n<12;n++)await new Promise(r=>setImmediate(r));};
  return {root,nodes,calls,flush,async advance(amount){clock+=amount;for(const [id,timer]of [...timers])if(timer.at<=clock){timers.delete(id);timer.fn();}await flush();}};
}
test('desktop migration cleans only owned resources and leaves record APIs untouched',async()=>{
  const e=environment({registrations:['http://127.0.0.1:8000/','https://other.invalid/']});await e.flush();await e.advance(100);
  assert.deepEqual(e.calls.unregister,['http://127.0.0.1:8000/']);assert.deepEqual(e.calls.deleted,['shikuang-static-old']);assert.equal(e.root.ShikuangDesktop.ready,true);assert.equal(e.root.document.documentElement.hasAttribute('data-desktop-starting'),false);
  assert.equal(e.root.indexedDB,undefined); // Bridge has no record deletion/migration API.
});
test('ready app with stalled resource migration still shows bounded recovery',async()=>{
  const e=environment({pending:true});await e.advance(15000);assert.match(e.root.ShikuangDesktop.bootError,/15 秒/);assert.equal(e.root.document.documentElement.hasAttribute('data-desktop-starting'),false);assert.ok(e.nodes.get('desktop-retry').onclick);
});
test('old controlled local page reloads before unlocking user input',async()=>{
  const e=environment({controller:{scriptURL:'http://127.0.0.1:8000/sw.js'}});await e.flush();assert.equal(e.calls.reload,1);assert.equal(e.root.ShikuangDesktop.assetsReady,undefined);assert.equal(e.root.document.documentElement.hasAttribute('data-desktop-starting'),true);
});
test('offline action checks bundled engine/reference instead of Web cache preparation',async()=>{
  const e=environment();await e.flush();await e.advance(100);const button=e.nodes.get('offlineBtn');const listener=button.listeners.find(x=>x.name==='click');assert.equal(listener.capture,true);
  let stopped=0;listener.handler({preventDefault(){},stopImmediatePropagation(){stopped++;}});assert.equal(button.disabled,true);await e.flush();assert.equal(stopped,1);assert.equal(e.calls.prepare,1);assert.equal(e.calls.reference[0].retry,true);assert.equal(button.disabled,false);assert.match(e.nodes.get('offlineProgress').textContent,/已在本机/);
});
