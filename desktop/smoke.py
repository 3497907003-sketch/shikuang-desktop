"""Fixed real-image package smoke. Not training, calibration, or blind accuracy."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import sys
import threading
import time
from pathlib import Path

CONTRACT = 'shikuang-owned-nonce-proof-first-smoke-v2'
PRIVATE_BACKUP_LIMIT = 128 * 1024 * 1024


def record_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Fa-f0-9-]{16,64}', run_id):
        raise ValueError('Smoke requires a bounded unique run nonce')
    return 'desktop-runtime-smoke-' + run_id


def source_identity(manifest: dict) -> dict:
    return {'contract': CONTRACT, 'script_sha256': hashlib.sha256(SCRIPT.encode()).hexdigest(),
            **{key: manifest[key] for key in ('app_version', 'model_manifest_sha256', 'web_commit')}}


def private_backup_path(report: Path, run_id: str) -> Path:
    return report.parent / 'private-backup' / (record_id(run_id) + '.json')


def validate_private_backup(payload: dict, manifest: dict, run_id: str) -> dict:
    """Bind saved canonical UTF-8 JSON text to its baseline without reserializing
    JS numbers in Python. This validates only local private backup content.
    """
    keys = {'schema', 'encoding', 'run_id', 'record_id', 'source_identity', 'baseline',
            'records', 'local_storage', 'search_raw'}
    if (not isinstance(payload, dict) or set(payload) != keys
            or payload['schema'] != CONTRACT + '-private-backup-v2'
            or payload['encoding'] != 'canonical-json-utf8-v1'
            or payload['run_id'] != run_id or payload['record_id'] != record_id(run_id)
            or payload['source_identity'] != source_identity(manifest)
            or any(not isinstance(payload[key], str) for key in ('records', 'local_storage', 'search_raw'))):
        raise ValueError('Private backup nonce/SOURCE/encoding mismatch')
    records = json.loads(payload['records'])
    local = json.loads(payload['local_storage'])
    search = json.loads(payload['search_raw'])
    if (not isinstance(records, list) or any(not isinstance(row, dict) or row.get('id') == record_id(run_id) for row in records)
            or not isinstance(local, list) or any(not isinstance(row, list) or len(row) != 2
                or not isinstance(row[0], str) or (row[1] is not None and not isinstance(row[1], str)) for row in local)):
        raise ValueError('Private backup record/local-storage structure mismatch')
    names = [row[0] for row in local]
    if (len(names) != len(set(names)) or names != sorted(names, key=lambda value: value.encode('utf-16-be', 'surrogatepass'))
            or search != dict(local).get('shikuang-searches-v2')):
        raise ValueError('Private backup local-storage/search binding mismatch')
    baseline = {'schema': 'shikuang-user-baseline-v2', 'record_count': len(records),
                **{key + '_sha256': hashlib.sha256(payload[field].encode('utf-8')).hexdigest()
                   for key, field in (('records', 'records'), ('local_storage', 'local_storage'), ('search_raw', 'search_raw'))}}
    if payload['baseline'] != baseline:
        raise ValueError('Private backup content digest does not match baseline')
    return baseline


def expected_write_snapshot(path: Path, manifest: dict, run_id: str, host: dict) -> dict:
    """Verify prior proof and bounded local private backup without logging payload."""
    path = path.resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
        raise ValueError('Write smoke report missing or larger than 8MiB')
    with path.open('rb') as stream:
        body = stream.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('Write smoke report grew beyond 8MiB')
    report = json.loads(body.decode('utf-8-sig'))
    if not isinstance(report, dict) or report.get('passed') is not True or report.get('phase') != 'write' or report.get('run_id') != run_id or report.get('errors') != []:
        raise ValueError('Write smoke report is not a successful matching run')
    for key in ('app_version', 'model_manifest_sha256', 'web_commit'):
        if report.get(key) != manifest[key]:
            raise ValueError('Write smoke report has another package identity: ' + key)
    if report.get('smoke_source_identity') != source_identity(manifest):
        raise ValueError('Write report has another smoke SOURCE identity')
    previous_host = report.get('host', {})
    if (not isinstance(previous_host, dict) or previous_host.get('run_id') != run_id or previous_host.get('phase') != 'write'
            or type(previous_host.get('pid')) is not int or previous_host['pid'] <= 0
            or not isinstance(previous_host.get('launch_id'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', previous_host.get('launch_id', ''))
            or previous_host['launch_id'] == host.get('launch_id')):
        raise ValueError('Write report lacks a distinct process launch identity')
    persistence = report.get('persistence', {})
    if not isinstance(persistence, dict):
        raise ValueError('Write report persistence must be an object')
    snapshot = persistence.get('snapshot', {})
    if (persistence.get('mode') != 'write-content-snapshot'
            or not isinstance(snapshot, dict)
            or snapshot.get('schema') != 'shikuang-persistence-snapshot-v2'
            or snapshot.get('record_id') != record_id(run_id)
            or type(snapshot.get('photo_count')) is not int or snapshot['photo_count'] != 1
            or snapshot.get('sample_id') != 'desktop-smoke-' + run_id):
        raise ValueError('Write report lacks owned nonce/photo persistence')
    for key in ('record_sha256', 'photos_sha256'):
        if not isinstance(snapshot.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot[key]):
            raise ValueError('Write report persistence digest invalid: ' + key)
    baseline = report.get('baseline')
    backup = report.get('private_backup')
    expected_path = private_backup_path(path, run_id)
    if (not isinstance(backup, dict) or set(backup) != {'path', 'bytes', 'sha256'}
            or backup['path'] != str(expected_path) or type(backup['bytes']) is not int
            or not 0 < backup['bytes'] <= PRIVATE_BACKUP_LIMIT
            or expected_path.is_symlink() or expected_path.resolve() != expected_path
            or (os.name != 'nt' and expected_path.stat().st_mode & 0o077)):
        raise ValueError('Owned private backup proof missing or unsafe')
    with expected_path.open('rb') as stream:
        private = stream.read(PRIVATE_BACKUP_LIMIT + 1)
    if len(private) != backup['bytes'] or hashlib.sha256(private).hexdigest() != backup['sha256']:
        raise ValueError('Owned private backup bytes changed')
    saved_baseline = validate_private_backup(json.loads(private), manifest, run_id)
    if (not isinstance(baseline, dict) or saved_baseline != baseline
            or snapshot.get('baseline') != baseline):
        raise ValueError('Owned baseline/backup/SOURCE proof mismatch')
    return {'snapshot': snapshot, 'baseline': baseline, 'private_backup': backup,
            'source_identity': source_identity(manifest), 'report': {'path': str(path), 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest()}}


class Smoke:
    def __init__(self, window, report: Path, fixtures: Path, phase: str, manifest: dict,
                 *, write_report: Path | None = None, run_id: str | None = None, host: dict | None = None):
        self.window = window
        self.report = report
        self.phase = phase
        self.manifest = manifest
        self.run_id = run_id or secrets.token_hex(16)
        self.record_id = record_id(self.run_id)
        self.host = dict(host or {})
        if phase == 'restart' and write_report is None:
            raise ValueError('Restart requires this run nonce and matching write proof')
        self.expected_persistence = expected_write_snapshot(write_report, manifest, self.run_id, self.host) if write_report else None
        self.private_backup = self.expected_persistence['private_backup'] if self.expected_persistence else None
        self.baseline = self.expected_persistence['baseline'] if self.expected_persistence else None
        self.success = False
        self.finished = threading.Event()
        self.fixtures = {}
        for name in ['quartz', 'dog']:
            data = (fixtures / (name + '.jpg')).read_bytes()
            if len(data) > 10 * 1024 * 1024 or not data.startswith(b'\xff\xd8'):
                raise ValueError('Smoke fixture must be a bounded real JPEG: ' + name)
            self.fixtures[name] = 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')

    def finish(self, result):
        if self.finished.is_set():
            return
        self.finished.set()
        if not isinstance(result, dict):
            result = {'passed': False, 'failure': 'Smoke callback returned no object'}
        if result.get('passed') is True and result.get('errors') != []:
            result.update(passed=False, failure='Smoke page errors cannot form a successful receipt')
        if self.expected_persistence:
            actual = result.get('persistence', {})
            if (not isinstance(actual, dict) or actual.get('mode') != 'full-content-sha256-before-any-put'
                    or actual.get('verified_before_any_put') is not True
                    or actual.get('cleanup_verified') is not True
                    or actual.get('snapshot') != self.expected_persistence['snapshot']):
                result.update(passed=False, failure='Full restart persistence receipt missing or changed')
        result.update({'platform': platform.platform(), 'architecture': platform.machine(),
            'app_version': self.manifest['app_version'], 'model_manifest_sha256': self.manifest['model_manifest_sha256'],
            'web_commit': self.manifest['web_commit'], 'phase': self.phase,
            'accuracy_claim': False, 'physicalNetworkDisabled': False,
            'run_id': self.run_id, 'host': self.host, 'smoke_source_identity': source_identity(self.manifest),
            'private_backup': self.private_backup, 'baseline': self.baseline})
        self.success = result.get('passed') is True
        self.report.parent.mkdir(parents=True, exist_ok=True)
        completed = threading.Event()
        completion_lock = threading.Lock()
        def complete():
            with completion_lock:
                if completed.is_set():
                    return
                completed.set()
            self.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self.window.destroy()
        if self.success and sys.platform == 'darwin':
            # Snapshot the actual native WKWebView, including the source-bound
            # gallery UI. This is not a reconstructed HTML or Canvas screenshot.
            try:
                from PyObjCTools import AppHelper
                from webview.platforms.cocoa import BrowserView
                import AppKit
                import WebKit
            except Exception as failure:
                result['nativeScreenshotLimitation'] = str(failure)
                complete()
                return
            def captured(image, error):
                if completed.is_set():
                    return
                snapshot_deadline.cancel()
                try:
                    if error or image is None:
                        raise RuntimeError('WKWebView snapshot unavailable')
                    bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
                    path = self.report.with_suffix('.png')
                    if not data.writeToFile_atomically_(str(path), True):
                        raise RuntimeError('WKWebView snapshot could not be saved')
                    result['nativeScreenshot'] = str(path)
                except Exception as failure:
                    result['nativeScreenshotLimitation'] = str(failure)
                complete()
            def snapshot():
                try:
                    config = WebKit.WKSnapshotConfiguration.alloc().init()
                    BrowserView.instances[self.window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(config, captured)
                except Exception as failure:
                    result['nativeScreenshotLimitation'] = str(failure)
                    complete()
            def expired():
                result['nativeScreenshotLimitation'] = 'Native WK snapshot did not finish within 10 seconds; functional checks completed separately'
                complete()
            snapshot_deadline = threading.Timer(10, expired)
            snapshot_deadline.daemon = True
            snapshot_deadline.start()
            AppHelper.callAfter(snapshot)
        else:
            complete()

    def run(self):
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.window.evaluate_js('!!(window.__shikuangAppReady && window.ShikuangDesktop && window.ShikuangDesktop.assetsReady)'):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('Desktop page/migration did not become ready')
            # A native WebKit content rule blocks external HTTP(S), including an
            # actual fetch probe. This isolates the App, not the host's interfaces.
            # CI can separately disable Windows adapters; report that independently.
            blocked = False
            if sys.platform == 'darwin':
                import WebKit
                from webview.platforms.cocoa import BrowserView
                ready = threading.Event()
                result = {}
                rules = json.dumps([{'trigger': {'url-filter': '^https?://', 'unless-domain': ['127.0.0.1']}, 'action': {'type': 'block'}}])
                def installed(rule, error):
                    # pywebview 6.2.1 native is NSWindow, not WKWebView. Keep all
                    # ObjC callback exceptions inside Python (otherwise AppKit aborts).
                    try:
                        if not rule:
                            raise RuntimeError('Content rule compilation failed')
                        BrowserView.instances[self.window.uid].webview.configuration().userContentController().addContentRuleList_(rule)
                        result['installed'] = True
                    except Exception as failure:
                        result['failure'] = str(failure)
                    finally:
                        ready.set()
                WebKit.WKContentRuleListStore.defaultStore().compileContentRuleListForIdentifier_encodedContentRuleList_completionHandler_('shikuang-runtime-offline-smoke', rules, installed)
                if not ready.wait(10):
                    raise RuntimeError('External network blocking rule did not install')
                if not result.get('installed'):
                    raise RuntimeError(result.get('failure', 'External network blocking rule failed'))
                blocked = True
            controls = {'phase': self.phase, 'externalHttpRuleInstalled': blocked,
                        'runId': self.run_id, 'recordId': self.record_id,
                        'sourceIdentity': source_identity(self.manifest)}
            if self.phase == 'write':
                backup_ready = threading.Event()
                backup_status = {}
                def backup_received(payload):
                    try:
                        baseline = validate_private_backup(payload, self.manifest, self.run_id)
                        raw = (json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
                        if len(raw) > PRIVATE_BACKUP_LIMIT:
                            raise ValueError('Private baseline exceeds 128MiB; no smoke record was written')
                        target = private_backup_path(self.report, self.run_id)
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        if target.parent.is_symlink() or (os.name != 'nt' and target.parent.stat().st_mode & 0o077):
                            raise ValueError('Private backup directory must be owner-only')
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                        self.private_backup = {'path': str(target), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                        self.baseline = baseline
                        backup_status['ok'] = True
                    except Exception as error:
                        backup_status['error'] = str(error)
                    finally:
                        backup_ready.set()
                self.window.evaluate_js('(' + SCRIPT + ')(' + json.dumps(dict(controls, operation='backup')) + ')', callback=backup_received)
                if not backup_ready.wait(30) or not backup_status.get('ok'):
                    raise RuntimeError(backup_status.get('error', 'Private baseline backup exceeded 30 seconds'))
            args = json.dumps(dict(controls, operation='run', images=self.fixtures,
                              expectedPersistence=self.expected_persistence, privateBackup=self.private_backup,
                              baseline=self.baseline), ensure_ascii=False)
            self.window.evaluate_js('(' + SCRIPT + ')(' + args + ')', callback=self.finish)
            if not self.finished.wait(300):
                self.finish({'passed': False, 'failure': 'Smoke exceeded 300 seconds'})
        except Exception as error:
            self.finish({'passed': False, 'failure': str(error)})


SCRIPT = r"""async function(input) {
 const report={passed:false,checks:[],errors:[],userAgent:navigator.userAgent,origin:location.origin,secureContext:isSecureContext,online:navigator.onLine,externalHttpRuleInstalled:input.externalHttpRuleInstalled};
 const check=(name,passed,details)=>{report.checks.push({name,passed,details});if(!passed)throw Error(name+': '+JSON.stringify(details));};
 const id='desktop-runtime-smoke-'+input.runId,strongRestart=input.phase==='restart'&&!!input.expectedPersistence;
 report.persistence={mode:input.phase==='write'?'write-content-snapshot':'full-content-sha256-before-any-put',verified_before_any_put:false};
 const canonical=value=>JSON.stringify(value,(_key,item)=>{if(item&&typeof item==='object'&&!Array.isArray(item)){const sorted={};for(const key of Object.keys(item).sort())sorted[key]=item[key];return sorted;}return item;});
 const digestText=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),b=>b.toString(16).padStart(2,'0')).join('');
 const digest=value=>digestText(canonical(value));
 const localSnapshot=()=>Array.from({length:localStorage.length},(_,i)=>localStorage.key(i)).sort().map(key=>[key,localStorage.getItem(key)]);
 const capture=async()=>({records:await SpecimenStore.list(),local_storage:localSnapshot()});
 const encodedCapture=async captured=>{const records=captured.records.filter(r=>r.id!==id),local=captured.local_storage;
   const recordText=canonical(records),localText=canonical(local),searchText=canonical(local.find(row=>row[0]==='shikuang-searches-v2')?.[1]??null);
   return {encoding:'canonical-json-utf8-v1',records:recordText,local_storage:localText,search_raw:searchText,baseline:{schema:'shikuang-user-baseline-v2',record_count:records.length,records_sha256:await digestText(recordText),local_storage_sha256:await digestText(localText),search_raw_sha256:await digestText(searchText)}};};
 const baseline=async()=> (await encodedCapture(await capture())).baseline;
 const snapshot=async()=>{const captured=await capture(),record=captured.records.find(r=>r.id===id);check('persistence-record-present',!!record,{});
   check('persistence-photos-populated',Array.isArray(record.photos)&&record.photos.length===1&&record.photoCount===1,{});
   check('persistence-owned-nonce',record.sampleId==='desktop-smoke-'+input.runId,{});
   return {schema:'shikuang-persistence-snapshot-v2',record_id:id,record_sha256:await digest(record),photos_sha256:await digest(record.photos),photo_count:record.photos.length,sample_id:record.sampleId,baseline:(await encodedCapture(captured)).baseline};};
 addEventListener('error',e=>report.errors.push(String(e.message)));
 addEventListener('unhandledrejection',e=>report.errors.push(String(e.reason)));
 try {
   check('exact-owned-nonce',typeof input.runId==='string'&&/^[A-Fa-f0-9-]{16,64}$/.test(input.runId)&&input.recordId===id,{});
   check('exact-smoke-source',input.sourceIdentity&&input.sourceIdentity.contract==='shikuang-owned-nonce-proof-first-smoke-v2'&&/^[a-f0-9]{64}$/.test(input.sourceIdentity.script_sha256),{});
   check('original-stable-origin',location.origin==='http://127.0.0.1:8000',location.origin);
   check('secure-local-context',isSecureContext,{});
   if(input.operation==='backup'){
     check('backup-write-phase',input.phase==='write',{});
     const captured=await capture();check('nonce-id-is-fresh',!captured.records.some(r=>r.id===id),{});
     return {schema:'shikuang-owned-nonce-proof-first-smoke-v2-private-backup-v2',run_id:input.runId,record_id:id,source_identity:input.sourceIdentity,...await encodedCapture(captured)};
   }
   check('private-backup-proof-present',input.privateBackup&&Number.isSafeInteger(input.privateBackup.bytes)&&input.privateBackup.bytes>0&&/^[a-f0-9]{64}$/.test(input.privateBackup.sha256)&&input.baseline&&input.baseline.schema==='shikuang-user-baseline-v2',{});
   check('all-existing-records-and-local-storage-unchanged-before-actions',canonical(await baseline())===canonical(input.baseline),{});
   if(input.phase==='restart'){
     check('matching-restart-proof-required',strongRestart&&canonical(input.expectedPersistence.source_identity)===canonical(input.sourceIdentity)&&canonical(input.expectedPersistence.private_backup)===canonical(input.privateBackup)&&canonical(input.expectedPersistence.baseline)===canonical(input.baseline),{});
     const actual=await snapshot();check('history-survives-restart',canonical(actual)===canonical(input.expectedPersistence.snapshot),{mode:report.persistence.mode});report.persistence.snapshot=actual;report.persistence.expected_write_report=input.expectedPersistence.report;report.persistence.verified_before_any_put=true;
   }else{
     check('write-nonce-absent-before-actions',input.phase==='write'&&!(await SpecimenStore.list()).some(r=>r.id===id),{});
   }
   if(input.externalHttpRuleInstalled){let reached=false;try{await fetch('https://example.com/',{signal:AbortSignal.timeout(3000)});reached=true;}catch(_){}check('external-http-blocked',!reached,{});}
   const contract=await MineralEngine.prepare();
   check('manifest-bound-models',MineralEngine.getModels(true).length===contract.modes.ensemble.length,{version:contract.version,labels:contract.labels.length,modelIds:contract.modes.ensemble});
   const controller=new AbortController();let cancelled=false,cancelStage='';
   const cancelDeadline=setTimeout(()=>{cancelStage='deadline';controller.abort();},30000);
   try{await MineralEngine.analyze([{src:input.images.quartz,crop:1}],{precision:false,signal:controller.signal,report:m=>{if(!controller.signal.aborted && /读取|输入筛查|分析图像/.test(m)){cancelStage=m;controller.abort();}}});}catch(e){cancelled=e.name==='AbortError';}finally{clearTimeout(cancelDeadline);}
   check('cancel-active-worker',cancelled && cancelStage && cancelStage!=='deadline',{stage:cancelStage});
   const logs=[];
   const started=performance.now();
   const quartz=await MineralEngine.analyze([{src:input.images.quartz,crop:1}],{precision:true,report:m=>logs.push(m)});
   report.quartzAnalysisMs=performance.now()-started;
   check('real-quartz-recognition',quartz.key==='quartz' && quartz.engine==='onnx-worker-wasm',{key:quartz.key,status:quartz.status,score:quartz.score,modelVersion:quartz.modelVersion,modelCount:quartz.modelCount,engine:quartz.engine});
   report.quartz=quartz;report.progress=logs;
   const dogStarted=performance.now();
   let dogError='';try{await MineralEngine.analyze([{src:input.images.dog,crop:1}],{precision:false});}catch(e){dogError=e.message;}report.dogAnalysisMs=performance.now()-dogStarted;
   check('real-dog-rejection',dogError==='这不是矿物，请您放入清晰的矿物照片。',{message:dogError});
   await MineralReference.load();
   check('complete-local-reference-library',window.MINERAL_CATALOG.minerals.length===6239,{species:window.MINERAL_CATALOG.minerals.length,referenceStats:window.MINERAL_CRYSTAL_RECORDS.stats});
   const waitFor=async(test,label)=>{const deadline=performance.now()+20000;while(performance.now()<deadline){const value=test();if(value)return value;await new Promise(r=>setTimeout(r,50));}throw Error(label+' timed out');};
   const galleryResponse=await fetch('./data/reviewed-photo-assets.json');
   const galleryManifest=await galleryResponse.json();
   const assets=MineralGallery.validateAssets(galleryManifest);
   const localRows=MINERAL_IMAGE_SOURCES.records.filter(r=>r.local_image_url);
   check('full-117-photo-manifest',assets.length===117 && localRows.length===117 && localRows.every(r=>assets.some(a=>a.path===r.local_image_url && a.bytes===r.bytes && a.sha256===r.sha256)),{assets:assets.length,records:localRows.length});
   const galleryBefore=await SpecimenStore.list();
   location.hash='library';
   await waitFor(()=>!document.getElementById('view-library').hidden,'library route');
   await waitFor(()=>document.getElementById('catalogIntro').textContent.includes('6239'),'complete library UI index');
   const search=document.getElementById('librarySearch');search.value='Quartz';search.dispatchEvent(new Event('input',{bubbles:true}));
   const card=await waitFor(()=>Array.from(document.querySelectorAll('#view-library [data-detail]')).find(b=>/^(?:quartz|catalog:ima-quartz)$/.test(b.dataset.detail)),'quartz card');
   const openGallery=async()=>{card.click();const section=await waitFor(()=>document.querySelector('#detailDialog[open] .image-section'),'quartz gallery');section.open=true;section.dispatchEvent(new Event('toggle'));await waitFor(()=>Array.from(section.querySelectorAll('img')).filter(i=>i.complete && i.naturalWidth>=224 && i.naturalHeight>=224).length===3,'3 real gallery images');return section;};
   const section=await openGallery();
   const decoded=Array.from(section.querySelectorAll('img')).map(i=>({id:i.id,width:i.naturalWidth,height:i.naturalHeight,src:i.currentSrc}));
   check('quartz-three-native-gallery-decodes',decoded.length===3 && decoded.every(i=>i.src.startsWith('blob:') && Math.min(i.width,i.height)>=224),decoded);
   document.getElementById('closeDetail').click();
   await waitFor(()=>!document.getElementById('detailDialog').open,'gallery close');
   const galleryAfter=await SpecimenStore.list();
   check('gallery-does-not-write-specimens',JSON.stringify(galleryBefore)===JSON.stringify(galleryAfter),{before:galleryBefore.length,after:galleryAfter.length});
   const recordsBefore=await SpecimenStore.list();
   if(input.phase==='write'){
     check('smoke-write-does-not-overwrite-existing-fixture',!recordsBefore.some(r=>r.id===id),{});
     check('smoke-photo-fits-record-storage',typeof input.images.quartz==='string'&&input.images.quartz.length<250000,{characters:input.images.quartz.length});
     check('existing-content-unchanged-before-owned-put',canonical(await baseline())===canonical(input.baseline),{});
     const record={...quartz,id,time:Date.now(),name:'桌面运行测试记录',scene:'lab',sampleId:'desktop-smoke-'+input.runId,photos:[input.images.quartz],photoCount:1,notes:'固定运行测试，非盲测'};
     await SpecimenStore.put(record);
   }
   // Restart never re-seeds: use the record read from storage for the backup.
   const record=(await SpecimenStore.list()).find(r=>r.id===id);check('smoke-record-still-present',!!record,{});
   const backup=JSON.stringify({schema:'shikuang-records',version:2,records:[record]});
   const parsed=MineralCore.parseImport(backup);
   check('backup-retains-model-identity',parsed.length===1 && parsed[0].manifestSha256===quartz.manifestSha256,{count:parsed.length});
   const recordsAfter=await SpecimenStore.list();
   check('existing-records-retained',canonical(await baseline())===canonical(input.baseline),{existingCount:input.baseline.record_count,fullContentVerified:true});
   if(input.phase==='restart'){
     check('owned-content-proof-before-cleanup',record.id===id&&record.sampleId==='desktop-smoke-'+input.runId&&canonical(await snapshot())===canonical(input.expectedPersistence.snapshot),{});
     await SpecimenStore.remove(id);check('smoke-record-cleanup',!(await SpecimenStore.list()).some(r=>r.id===id),{});
     check('all-existing-content-restored-after-own-delete',canonical(await baseline())===canonical(input.baseline),{});report.persistence.cleanup_verified=true;
   }
   report.resources=performance.getEntriesByType('resource').map(r=>({name:r.name,duration:r.duration}));
   check('bundled-resources-only',report.resources.every(r=>r.name.startsWith(location.origin+'/')||r.name.startsWith('blob:')||r.name==='https://example.com/'),{});
   await openGallery();
   document.querySelector('#detailDialog .image-section').scrollIntoView();
   // WKWebView pauses animation frames while the macOS screen is locked. The
   // functional decode checks stay strict; visual synchronization is reported
   // separately and must not hang package shutdown in that environment.
   report.galleryFrameSynchronized=false;
   await Promise.race([new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>{report.galleryFrameSynchronized=true;r();}))),new Promise(r=>setTimeout(r,1000))]);
   check('all-existing-content-exact-at-completion',canonical(await baseline())===canonical(input.baseline),{});
   if(input.phase==='write')report.persistence.snapshot=await snapshot();
   report.passed=true;
 }catch(e){report.failure=String(e);}
 return report;
}"""
