"""Fixed real-image package smoke. Not training, calibration, or blind accuracy."""
from __future__ import annotations

import base64
import json
import platform
import sys
import threading
import time
from pathlib import Path


class Smoke:
    def __init__(self, window, report: Path, fixtures: Path, phase: str, manifest: dict):
        self.window = window
        self.report = report
        self.phase = phase
        self.manifest = manifest
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
        result.update({'platform': platform.platform(), 'architecture': platform.machine(),
            'app_version': self.manifest['app_version'], 'model_manifest_sha256': self.manifest['model_manifest_sha256'],
            'web_commit': self.manifest['web_commit'], 'phase': self.phase,
            'accuracy_claim': False, 'physicalNetworkDisabled': False})
        self.success = result.get('passed') is True
        self.report.parent.mkdir(parents=True, exist_ok=True)
        def complete():
            self.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self.window.destroy()
        if self.success and sys.platform == 'darwin':
            # Snapshot the actual native WKWebView, including the source-bound
            # gallery UI. This is not a reconstructed HTML or Canvas screenshot.
            from PyObjCTools import AppHelper
            from webview.platforms.cocoa import BrowserView
            import AppKit
            import WebKit
            def captured(image, error):
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
            args = json.dumps({'images': self.fixtures, 'phase': self.phase, 'externalHttpRuleInstalled': blocked}, ensure_ascii=False)
            self.window.evaluate_js('(' + SCRIPT + ')(' + args + ')', callback=self.finish)
            if not self.finished.wait(300):
                self.finish({'passed': False, 'failure': 'Smoke exceeded 300 seconds'})
        except Exception as error:
            self.finish({'passed': False, 'failure': str(error)})


SCRIPT = r"""async function(input) {
 const report={passed:false,checks:[],errors:[],userAgent:navigator.userAgent,origin:location.origin,secureContext:isSecureContext,online:navigator.onLine,externalHttpRuleInstalled:input.externalHttpRuleInstalled};
 const check=(name,passed,details)=>{report.checks.push({name,passed,details});if(!passed)throw Error(name+': '+JSON.stringify(details));};
 addEventListener('error',e=>report.errors.push(String(e.message)));
 addEventListener('unhandledrejection',e=>report.errors.push(String(e.reason)));
 try {
   check('original-stable-origin',location.origin==='http://127.0.0.1:8000',location.origin);
   check('secure-local-context',isSecureContext,{});
   if(input.externalHttpRuleInstalled){let reached=false;try{await fetch('https://example.com/',{signal:AbortSignal.timeout(3000)});reached=true;}catch(_){}check('external-http-blocked',!reached,{});}
   const contract=await MineralEngine.prepare();
   check('manifest-bound-models',MineralEngine.getModels(true).length===contract.modes.ensemble.length,{version:contract.version,labels:contract.labels.length,modelIds:contract.modes.ensemble});
   const id='desktop-runtime-smoke-record';
   if(input.phase==='restart')check('history-survives-restart',(await SpecimenStore.list()).some(r=>r.id===id),{});
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
   const record={...quartz,id,time:Date.now(),name:'桌面运行测试记录',scene:'lab',photos:[],photoCount:0,notes:'固定运行测试，非盲测'};
   await SpecimenStore.put(record);
   const backup=JSON.stringify({schema:'shikuang-records',version:2,records:[record]});
   const parsed=MineralCore.parseImport(backup);
   check('backup-retains-model-identity',parsed.length===1 && parsed[0].manifestSha256===quartz.manifestSha256,{count:parsed.length});
   const recordsAfter=await SpecimenStore.list();
   check('existing-records-retained',recordsBefore.every(r=>recordsAfter.some(x=>x.id===r.id)),{existingCount:recordsBefore.length});
   if(input.phase==='restart'){await SpecimenStore.remove(id);check('smoke-record-cleanup',!(await SpecimenStore.list()).some(r=>r.id===id),{});}
   report.resources=performance.getEntriesByType('resource').map(r=>({name:r.name,duration:r.duration}));
   check('bundled-resources-only',report.resources.every(r=>r.name.startsWith(location.origin+'/')||r.name.startsWith('blob:')||r.name==='https://example.com/'),{});
   await openGallery();
   document.querySelector('#detailDialog .image-section').scrollIntoView();
   await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
   report.passed=true;
 }catch(e){report.failure=String(e);}
 return report;
}"""
