# 桌面 2.5 持续要求

复用原 ca/desktop/run_app.py、Shikuang.spec 与 3497907003-sketch/shikuang-desktop。面向整个实验室及不同电脑；手机仍为主用端。沿用“识矿”，拍照、录像、照片文件和人工描述保持独立。

模型与 UI 来自同一个网页发布构建，不再保留旧 FastAPI / Torch / 拉伸缩放支路。ONNX、WASM、晶系中文显示、官方目录和图像来源元数据随包交付。版本和模型身份由 web-source.json 记录，模型 manifest 动态决定打包内容。当前继承权重不等于重训已完成；Kappa 0.95 是目标，必须独立评估。

保持 127.0.0.1:8000 原始 origin；本进程先绑定再启动 UI，使用随机身份健康响应，端口冲突明确退出。单实例锁跨平台并在退出后释放；不删除锁文件造成竞态。网络服务只监听 loopback，只允许清单内静态资源，禁止目录列表、路径穿越、跨 Host 访问及上传接口。

使用 private_mode=False。macOS 保留原 CFBundleIdentifier=Shikuang 和默认 WKWebsiteDataStore；Windows 使用旧 pywebview 默认持久目录。保持现有 IndexedDB、本地历史以及 Web UI 既有 1.0 历史迁移。旧隐私模式已经清除的数据不能恢复；更新前应导出备份。卸载 App 本体不会主动删除资料目录。

启动有健康检查与 15 秒界面看门狗，出错明确显示重试和版本/组件说明；不得自动打开浏览器冒充 App 启动成功。关闭窗口必须停止服务器并释放端口/锁。保存路径、读权限、空间不足和中文路径有明确错误。

macOS 使用系统 WKWebView；Python 和模型/WASM 自包含。Windows 固定版 WebView2 要随包交付，不能以安装了 Edge 或 CI 上已有运行时作离线安装证明。固定运行时需持续更新，并遵守 Microsoft 许可和 Windows 10 的 AppContainer 目录读取权限要求。

发布验收：本机真实石英/狗照片，Worker 引擎与清单身份，资源无外部下载，断网重启、存储跨启动保留和旧历史迁移，取消后重试、完整资料库离线读取，备份导出/导入，窗口关闭清理，未知服务占端口/重复启动的错误路径。真实测试照片仅为运行烟测，不属于新的盲测数据。

## 2026-10-28 桌面线程恢复资源复核

已用既有同步程序固定Web提交6f65a0c84a22dc8630d6e0432ff69dde13f4245e重建并同步168项离线资源，本轮只读verify_bundle及逐文件canonical哈希复核通过。4个既有模型及模型清单SHA ddbe27e79489df0ff63647b37c17f701e458a59e076df14d47d93873085ae7fa保持；未运行真实模型、桌面GUI或新安装器，未推送或发布。Android／桌面本地资源证据见根work/android-idle-worker-source-sync-resume34-001/Root-safe-Android-Desktop-local-sync-summary.json。
