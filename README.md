# 识矿桌面版 2.5 预览

在原有 Python / pywebview / PyInstaller 桌面版上升级。网页界面、Worker 推理、ONNX 模型、WASM、中文物性与资料库使用同一份已提交的构建，完整身份见 `web-source.json`。桌面端不再保留另一个 Torch 推理服务。

安装包内置模型、6,239 条官方矿物目录、晶胞与物性元数据及 117 张经来源审核的本地标本照片。启动和本机识别不需要先联网下载；官方 PDF、图片来源原页与用户自行配置的联网辅助需要联网。请先阅读 App 内的新用户指南，采用多角度照片和物性复核，将结果视为候选线索。

资料数量不等于识别类别数量。随包模型仍为继承的 98 个标签权重，新八类小样本重训未达到 Kappa 0.95，未进入正式包；旧数据发现训练与评估重复，旧分数不能用作独立准确率证明。真实石英与非矿物照片只用于桌面运行检查，不能证明实验室泛化准确率。详细来源与重训报告随共享网页资料保留。

本分支提供待验收的源码与 CI 构建产物，没有自动发布新版。正式安装包确认后放在本仓库 [Releases](https://github.com/3497907003-sketch/shikuang-desktop/releases)。旧版 Release 不会随本分支更新。

## 安装与历史

- macOS：当前本机构建为 Apple Silicon arm64，使用系统 WKWebView。把 `Shikuang.app` 放入“应用程序”，从 Finder 打开。当前仅临时签名，尚未取得 Developer ID 签名或公证；Intel Mac 没有构建验证。
- Windows：构建目标为 x64，完整解压后运行 `Shikuang/Shikuang.exe`，请保留旁边所有文件。包内提供固定版 WebView2，不依赖预装 Edge。已在 GitHub Windows Server 2022 x64 运行真实安装包并通过断网与重启门；Windows 10/11 实体电脑和相机仍需后续实测。
- 更新前在旧 App 的“标本”页面导出备份。程序保留原 `http://127.0.0.1:8000` 地址与 macOS `Shikuang` 应用身份，开启持久存储；不会主动删除历史。旧版隐私模式已经清除的数据无法恢复，浏览器、手机和桌面各自的数据也不会自动合并。必要时通过备份导入迁移。
- 已有程序或其他服务占用 8000 端口时会明确退出。关闭旧识矿窗口后重试；不会连接未知服务，也不会换地址造成历史看似消失。关闭窗口会停止本机静态服务并释放单实例锁。

## 构建与验证

需要 Python 3.11+，按 `requirements-desktop.txt` 安装固定依赖。先在 `3497907003-sketch/shikuang-app` 的 `web-source.json` 指定完整 commit 构建，再运行：

```sh
python tools/sync_web.py ../shikuang-web-2.0/dist --web-commit <完整源提交>
python tools/make_icons.py
python -m unittest discover -s tests -v
python desktop/run_app.py --verify-only
python -m PyInstaller --noconfirm Shikuang.spec
```

`sync_web.py` 将每一项构建资产与指定 commit 比对，核验模型及资料 SHA-256，并生成完整本地资源清单；拒绝过期构建、混入权重和缺失资料。Windows 构建还需运行 `tools/prepare_webview2.ps1`。CI 使用固定源提交并执行真实安装包启动、石英识别、非矿物拒判、取消后重试、资料库读取、关闭重启和历史保留。Windows 测试实际禁用外部网络适配器并检查无默认路由，最后恢复网络；macOS 本机测试在 WKWebView 层阻断外部 HTTP(S)，不是关闭整机网络。

固定版 Microsoft WebView2 155.0.4283.45 x64 的官方 CAB 为 310,544,938 字节，来源及 SHA 记录在 `vendor-sources.json`。构建保留完整运行时和其许可文件，验证微软可执行签名，按官方要求配置读取权限。该运行时不自动更新，需要随后续 App 发布维护；不得删掉组件缩减为依赖在线安装的包。[Microsoft 固定运行时分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

实际相机权限、系统文件选择与保存窗口、各实验室实体设备、Windows 10/11 和 Intel Mac 的交互需要分别验证。不得把自动化识别检查当成人工操作验证或准确率验收。

## Windows 草稿上传校验

`.github/workflows/draft-upload.yml` 只提供手动调度，不自动运行或公开发布。输入已经成功的 `desktop.yml` run、实际构建的桌面提交和预览 tag；当前仅接受经过验证的 `3f94d9a` 完整提交及 `v2.5.0-preview.1`。上传器自身的提交不冒充安装包的构建提交。

流程重新核验 Actions 外包摘要、安装包 SHA256/CRC/x64 PE、固定 WebView2 的全部 256 个文件、同一网页提交的 157 个资产，以及两个真实断网进程的矿物/非矿物、取消、图库、历史重启与本地资源证据。Windows 运行证据来自 Windows Server 2022 x64，未包含原生截图，不代表人工拍照、文件保存面板或实体 Windows 10/11 测试，也不是 Kappa 结果。上传仅增加 Windows ZIP、Windows 校验和与 gate 报告；同 tag 的公开 release、错误目标、不同已有 Windows 文件均会被拒绝，macOS 文件不会删除或覆盖。

GitHub 要求手动调度 workflow 已存在于默认分支。首次使用前需完成该流程文件的代码审查与合入；运行流程仍按输入的固定构建提交核验，而不是重新打包当前分支。
