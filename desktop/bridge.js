/* Trusted desktop wrapper only; the bundled Web app/Worker remain byte-identical.
 * ES5 recovery remains readable if a system WebView cannot parse the main app.
 */
(function (root) {
  'use strict';
  if (root.ShikuangDesktop) return;
  root.ShikuangDesktop = __DESKTOP_METADATA__;
  var document = root.document;
  document.documentElement.setAttribute('data-desktop-starting', 'true');
  var style = document.createElement('style');
  style.textContent = 'html[data-desktop-starting] body>*{visibility:hidden}html[data-desktop-starting] #desktop-starting-cover{visibility:visible;position:fixed;top:0;left:0;right:0;bottom:0;z-index:99999;display:flex;align-items:center;justify-content:center;background:#edf2f4;color:#143f40;font-family:Arial,sans-serif;font-size:20px}';
  (document.head || document.documentElement).appendChild(style);
  function cover() { if (!document.body) { root.setTimeout(cover, 20); return; } if (!document.documentElement.hasAttribute('data-desktop-starting')) return; var element=document.createElement('div');element.id='desktop-starting-cover';element.textContent='正在打开识矿本机资源…';document.body.appendChild(element); }
  cover();
  function uncover() { document.documentElement.removeAttribute('data-desktop-starting');var element=document.getElementById('desktop-starting-cover');if(element)element.remove(); }
  function recovery(message) {
    var body = root.document.body;
    if (!body) { root.setTimeout(function () { recovery(message); }, 50); return; }
    uncover();
    body.innerHTML = '<main style="max-width:34rem;margin:auto;padding:32px 22px;color:#143f40;font-family:Arial,sans-serif;line-height:1.8"><h1>识矿 · 启动需要处理</h1><p id="desktop-error"></p><p>请使用最新版完整安装包，并检查系统 WebView。macOS 请更新系统；Windows 使用随包的 WebView2。本机标本与历史记录保留。</p><button id="desktop-retry" style="padding:12px 24px;font-size:16px">重新打开界面</button></main>';
    root.document.getElementById('desktop-error').textContent = message;
    root.document.getElementById('desktop-retry').onclick = function () { root.location.reload(); };
    root.ShikuangDesktop.bootError = message;
  }
  var missing = [];
  if (!root.Promise || !root.URL || !root.AbortController) missing.push('现代浏览器组件');
  if (!root.Worker || !root.WebAssembly) missing.push('后台识别 / WASM');
  if (!root.crypto || !root.crypto.subtle) missing.push('资源校验');
  if (!root.Image || !root.Image.prototype.decode) missing.push('图片读取');
  try { new Function('var x={};return x?.value ?? 0;'); } catch (_) { missing.push('JavaScript 语法'); }
  if (missing.length) { recovery('当前系统缺少：' + missing.join('、') + '。'); return; }
  var timeout = root.setTimeout(function () { if (!root.__shikuangAppReady || !root.ShikuangDesktop.assetsReady) recovery('界面启动超过 15 秒，请重新加载；若仍失败，请重新安装完整版本。'); }, 15000);
  var owned = root.ShikuangDesktop.origin;
  var cleanup = root.navigator.serviceWorker ? root.navigator.serviceWorker.getRegistrations() : Promise.resolve([]);
  cleanup.then(function (registrations) {
    return Promise.all(registrations.map(function (registration) {
      return new URL(registration.scope).origin === owned ? registration.unregister() : false;
    }));
  }).then(function () { return root.caches ? root.caches.keys() : []; }).then(function (names) {
    return Promise.all(names.filter(function (name) { return name.indexOf('shikuang-') === 0; }).map(function (name) { return root.caches.delete(name); }));
  }).then(function () {
    if (root.navigator.serviceWorker && root.navigator.serviceWorker.controller && new URL(root.navigator.serviceWorker.controller.scriptURL).origin === owned) { root.location.reload(); return; }
    root.ShikuangDesktop.assetsReady = true;
  }).catch(function () { recovery('旧资源缓存未能完成迁移，请关闭识矿后重试。历史记录没有清除。'); });
  function decorate() {
    if (root.ShikuangDesktop.bootError) return;
    if (!root.__shikuangAppReady || !root.ShikuangDesktop.assetsReady) { root.setTimeout(decorate, 100); return; }
    root.clearTimeout(timeout);
    uncover();
    var offline = root.document.getElementById('offlineBtn');
    if (offline) {
      // Bundled desktop assets do not depend on CacheStorage or an external CDN.
      offline.textContent = '检查内置离线资源';
      offline.onclick = null;
      offline.addEventListener('click', function (event) {
        event.preventDefault();event.stopImmediatePropagation();
        var message = root.document.getElementById('offlineProgress');
        offline.disabled = true;
        if (message) message.textContent = '正在检查内置模型与完整资料库…';
        root.MineralEngine.prepare().then(function () { return root.MineralReference.load({ retry: true }); }).then(function () {
          if (message) message.textContent = '安装包模型、资料和 WASM 已在本机。官方 PDF、图片原页及联网辅助仍需联网。';
        }).catch(function (error) { if (message) message.textContent = '资源检查失败：' + String(error.message || error) + '。请重新安装完整包；历史保留。'; }).finally(function () { offline.disabled = false; });
      }, true);
    }
    var info = root.document.getElementById('offlineInfo');
    if (info) info.textContent = '桌面安装包内置全部模型、WASM 与资料，不需要先联网下载。';
    root.ShikuangDesktop.ready = true;
  }
  decorate();
})(window);
