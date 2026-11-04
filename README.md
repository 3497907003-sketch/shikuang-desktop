# 识矿桌面版 2.5 预览

在原有 Python / pywebview / PyInstaller 桌面版上升级。网页界面、Worker 推理、ONNX 模型、WASM、中文物性与资料库使用同一份已提交的构建，完整身份见 `web-source.json`。桌面端不再保留另一个 Torch 推理服务。

安装包内置模型、6,239 条官方矿物目录、晶胞与物性元数据及 117 张经来源审核的本地标本照片。启动和本机识别不需要先联网下载；官方 PDF、图片来源原页与用户自行配置的联网辅助需要联网。请先阅读 App 内的新用户指南，采用多角度照片和物性复核，将结果视为候选线索。

资料数量不等于识别类别数量。随包模型仍为继承的 98 个标签权重，本次重训尚无独立 Kappa 约 0.80 的验收证据，研究权重未进入正式包；旧数据发现训练与评估重复，旧分数不能用作独立准确率证明。真实石英与非矿物照片只用于桌面运行检查，不能证明实验室泛化准确率。详细来源与重训报告随共享网页资料保留。

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

共享网页现使用内容生成的缓存标识。同步器从指定不可变提交提取原始资产和构建工具，在临时目录重现该构建，再逐字节比对完整 dist，支持 HTML/Core/Worker 的合法构建变换；不直接信任待同步目录，也不放宽过期或篡改校验。构建工具身份同时记录在 `web-source.json`。资源同步及本地回归不代表新的桌面安装包、实体设备或识别准确率已验收。

固定版 Microsoft WebView2 155.0.4283.45 x64 的官方 CAB 为 310,544,938 字节，来源及 SHA 记录在 `vendor-sources.json`。构建保留完整运行时和其许可文件，验证微软可执行签名，按官方要求配置读取权限。该运行时不自动更新，需要随后续 App 发布维护；不得删掉组件缩减为依赖在线安装的包。[Microsoft 固定运行时分发说明](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

实际相机权限、系统文件选择与保存窗口、各实验室实体设备、Windows 10/11 和 Intel Mac 的交互需要分别验证。不得把自动化识别检查当成人工操作验证或准确率验收。

## Windows 开发包核验与发布边界

当前开发包身份以 `web-source.json`、模型和资源 manifest 为准：canonical 构建含 168 项离线资产、4 个继承模型（3 个分类器与 1 个输入筛查）及已审书籍来源表述。`tools/verify_windows_delivery.py` 的资产集合现从精确 Web 构建与经 SHA 固定的包内索引派生，候选构建证据必须显式绑定 CI run/attempt、桌面/Web 提交、版本/tag 及 artifact ID/digest/大小。`.github/workflows/draft-upload.yml` 已移除历史默认值，恢复为仅手动、显式证据调度。native-v2 适配复用既有公共校验，严格关联原始报告、主机网络注释后报告、nonce、进程与网络恢复记录；私有备份只在构建机验证，明确排除上传。旧包没有新原始/最终绑定证明会拒绝，不能将本地软件检查当作上传或验收通过。本次源码修正的本地回归不构成新安装包、真机或发布验收；尚未填入未经验证的候选构建证据。

现有开发 ZIP 已核验完整 SHA256/CRC、x64 PE、168 项 Web payload 与固定 WebView2 的全部 256 文件 seal。此处记录开发包静态身份与完整性证据；既有 Windows Server 2022 x64 VM 断网和重启烟测保持原范围，不代表安装器、实体 Windows 10/11、相机或保存对话框、识别准确率及独立 Kappa 验收。

正式发布仍须新权重独立验收、实体设备验证，以及绑定当前精确来源和已审产物的发布门；本段不构成上传或发布授权。已封存开发 ZIP 保持原样，工作树中的说明修订不会自动进入既有包。


Windows 候选上传的离线准备：审查者提供独立的 UTF-8 JSON 文件及其 SHA256，字段必须精确为 `schema_version`（1）、`repository`、正整数 `run_id` / `run_attempt`、40位 `source_commit` / `web_commit`、`app_version`、与版本相同的 `tag`、`asset_index_sha256` 和 `artifact`（精确 `id` / `size_in_bytes` / `digest`）。索引 SHA 取自同一实际构建的 `web/.web-assets.json`，并由 artifact 中的新 `windows-build-provenance.json` 固定到对应 CI attempt。该生成索引不属于 Git 跟踪文件，核验仍要从所固定 Web 提交重建全部资产并逐项比较，不能仅信任工作树索引。当前未建立新候选证据文件、没有调度或执行上传；正式候选仍须先完成当前运行协议和两轮多端回归。
