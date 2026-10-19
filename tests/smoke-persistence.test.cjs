// Execute the production smoke script against synthetic metadata/DOM fixtures.
// No browser, photographic decode, ONNX, Worker or recognition is exercised here.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../desktop/smoke.py'), 'utf8');
const script = source.match(/^SCRIPT = r"""([\s\S]*)"""\s*$/m)[1];
const id = 'desktop-runtime-smoke-record';
const runId = '01234567-89ab-cdef-0123-456789abcdef';
const clone = x => JSON.parse(JSON.stringify(x));
const record = () => ({id, name:'合成标本', notes:'应完整保留的字段', time:123,
  sampleId:'desktop-smoke-'+runId, photos:['data:image/jpeg;base64,SYNTHETIC-NOT-AN-IMAGE'], photoCount:1,
  manifestSha256:'a'.repeat(64), extra:{hardness:7}});
const history = () => [{query:'desktop-smoke-'+runId, kind:'records', time:123},
  {query:'先前搜索', kind:'library', time:100}];
function canonical(value) {
  if (Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if (value && typeof value==='object') return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
const sha = text => crypto.createHash('sha256').update(text,'utf8').digest('hex');
function expected(row=record(), searches=history(), raw=JSON.stringify(searches)) {
  return {snapshot:{schema:'shikuang-persistence-snapshot-v1',record_id:id,
    record_sha256:sha(canonical(row)),photos_sha256:sha(canonical(row.photos)),photo_count:1,
    search_sha256:sha(canonical(searches)),search_raw_sha256:sha(raw),search_fixture_query:row.sampleId},
    report:{path:'synthetic-write.json',bytes:100,sha256:'b'.repeat(64)}};
}
async function execute({phase='restart', rows=[record()], searches=history(), raw=JSON.stringify(searches),
                         expect=expected(), full=false}={}) {
  const calls={put:0,prepare:0,remove:0};
  const local = {'shikuang-searches-v2':raw};
  const store = clone(rows);
  const localStorage = {getItem:key=>local[key]??null,setItem:(key,value)=>{local[key]=String(value);}};
  const SearchHistory = {read:()=>clone(JSON.parse(local['shikuang-searches-v2']||'[]')),
    save:(query,kind)=>{localStorage.setItem('shikuang-searches-v2',JSON.stringify([{query,kind,time:456},...SearchHistory.read()]));}};
  const section={open:false,dispatchEvent(){},scrollIntoView(){},querySelectorAll:()=>[1,2,3].map(n=>({id:'synthetic-'+n,complete:true,naturalWidth:224,naturalHeight:224,currentSrc:'blob:synthetic-'+n}))};
  const dialog={open:false};
  const search={value:'',dispatchEvent(){}};
  const card={dataset:{detail:'quartz'},click(){dialog.open=true;}};
  const document={getElementById:name=>({'view-library':{hidden:false},catalogIntro:{textContent:'6239'},
    librarySearch:search,closeDetail:{click(){dialog.open=false;}},detailDialog:dialog}[name]),
    querySelectorAll:()=>[card],querySelector:query=>query.includes('[open]')?(dialog.open?section:null):section};
  const assets=Array.from({length:117},(_,i)=>({path:'synthetic-'+i,bytes:1,sha256:'a'}));
  const quartz={key:'quartz',engine:'onnx-worker-wasm',manifestSha256:'a'.repeat(64)}; // Contract fixture, no inference.
  const context={navigator:{userAgent:'synthetic-node-not-WebView',onLine:false},location:{origin:'http://127.0.0.1:8000'},
    isSecureContext:true,crypto:crypto.webcrypto,TextEncoder,Uint8Array,AbortController,Event,
    setTimeout:(fn,ms)=>setTimeout(fn,ms===1000?0:ms),clearTimeout,requestAnimationFrame:fn=>fn(),
    performance:{now:()=>0,getEntriesByType:()=>[]},addEventListener(){},localStorage,SearchHistory,
    SpecimenStore:{list:async()=>clone(store),put:async row=>{calls.put++;store.push(clone(row));},
      remove:async key=>{calls.remove++;store.splice(store.findIndex(r=>r.id===key),1);}},
    MineralEngine:{prepare:async()=>{calls.prepare++;if(!full)throw Error('SYNTHETIC-STOP-BEFORE-INFERENCE');return {version:'fixture',labels:[],modes:{ensemble:['fixture']}};},
      getModels:()=>['fixture'],analyze:async (photos,options)=>{
        if(options.signal){options.report('读取输入');const error=Error('synthetic cancel');error.name='AbortError';throw error;}
        if(photos[0].src==='synthetic-dog')throw Error('这不是矿物，请您放入清晰的矿物照片。');
        return clone(quartz);}},
    MineralReference:{load:async()=>{}},window:{MINERAL_CATALOG:{minerals:Array(6239)},MINERAL_CRYSTAL_RECORDS:{stats:{}}},
    fetch:async()=>({json:async()=>({})}),MineralGallery:{validateAssets:()=>assets},
    MINERAL_IMAGE_SOURCES:{records:assets.map(a=>({local_image_url:a.path,bytes:a.bytes,sha256:a.sha256}))},
    document,MineralCore:{parseImport:body=>JSON.parse(body).records}};
  const fn = vm.runInNewContext('('+script+')',context,{timeout:1000});
  const report = await fn({phase,runId,images:{quartz:'synthetic-quartz',dog:'synthetic-dog'},
                           externalHttpRuleInstalled:false,expectedPersistence:expect});
  return {report,calls,store,searches:SearchHistory.read(),raw:local['shikuang-searches-v2']};
}

test('matching full snapshot is verified before prepare and before all writes',async()=>{
  const {report,calls}=await execute();
  assert.equal(report.passed,false);
  assert.match(report.failure,/SYNTHETIC-STOP-BEFORE-INFERENCE/);
  assert.equal(report.persistence.verified_before_any_put,true);
  assert.deepEqual(clone(report.persistence.snapshot),expected().snapshot);
  assert.equal(calls.prepare,1);assert.equal(calls.put,0);
});

test('same ID with missing/changed fields, photos, search or raw storage fails before prepare',async()=>{
  const variants=[{rows:[]},{rows:[{...record(),notes:'lost field'}]},
    {rows:[{...record(),photos:[] ,photoCount:0}]},{rows:[{...record(),photos:['changed-bytes']}]},
    {rows:[{...record(),extra:{hardness:6}}]},
    {searches:history().map(r=>({...r,time:999}))},{searches:[]},
    {raw:JSON.stringify(history(),null,2)}];
  for(const variant of variants){
    const {report,calls}=await execute(variant);
    assert.equal(report.passed,false,JSON.stringify(variant));
    assert.equal(report.persistence.verified_before_any_put,false);
    assert.equal(calls.prepare,0);assert.equal(calls.put,0);assert.equal(calls.remove,0);
  }
});

test('write actually stores a populated photo and search; strong restart never re-seeds',async()=>{
  const previous={id:'keep-existing',notes:'do not delete',photos:['unrelated-synthetic']};
  const write=await execute({phase:'write',rows:[previous],searches:[history()[1]],expect:null,full:true});
  assert.equal(write.report.passed,true,write.report.failure);
  assert.equal(write.calls.put,1);assert.equal(write.calls.remove,0);
  const saved=write.store.find(r=>r.id===id);
  assert.deepEqual(saved.photos,['synthetic-quartz']);assert.equal(saved.photoCount,1);
  assert.equal(write.report.persistence.snapshot.photos_sha256,sha(canonical(saved.photos)));
  const restart=await execute({rows:write.store,searches:write.searches,raw:write.raw,
    expect:{snapshot:clone(write.report.persistence.snapshot),report:{path:'write.json',bytes:1,sha256:'a'.repeat(64)}},full:true});
  assert.equal(restart.report.passed,true,restart.report.failure);
  assert.equal(restart.report.persistence.verified_before_any_put,true);
  assert.equal(restart.calls.put,0);assert.equal(restart.calls.remove,1);
  assert.deepEqual(restart.store,[previous]);assert.deepEqual(restart.searches,[history()[1]]);
});

test('write refuses to replace a preexisting same ID or discard the twentieth search',async()=>{
  const same=await execute({phase:'write',rows:[record()],expect:null,full:true});
  assert.equal(same.report.passed,false);assert.equal(same.calls.put,0);
  const fullSearch=Array.from({length:20},(_,i)=>({query:String(i),kind:'library',time:i}));
  const full=await execute({phase:'write',rows:[],searches:fullSearch,expect:null,full:true});
  assert.equal(full.report.passed,false);assert.equal(full.calls.put,0);assert.deepEqual(full.searches,fullSearch);
});

test('legacy no-write-report mode is labeled ID-only and does not claim content verification',async()=>{
  const {report,calls}=await execute({rows:[{id}],expect:null});
  assert.equal(report.persistence.mode,'legacy-id-only');
  assert.equal(report.persistence.verified_before_any_put,false);
  assert.equal(calls.prepare,1);assert.equal(calls.put,0);
});
