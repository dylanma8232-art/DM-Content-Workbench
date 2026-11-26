<div align="center">

# 内容工作台（DM-Content-Workbench）

**把分散的素材变成有原文依据的稿件，也能逐个平台看账号运营表现。**

[![语言](https://img.shields.io/badge/语言-简体中文-c8521b?style=flat-square)](README.md) [![文档](https://img.shields.io/badge/文档-English-2b2927?style=flat-square)](README_EN.md) ![环境](https://img.shields.io/badge/Python-3.12-3776ab?style=flat-square) ![存储](https://img.shields.io/badge/存储-SQLite-38685c?style=flat-square) [![许可](https://img.shields.io/badge/License-CC_BY--NC--SA_4.0-b45309?style=flat-square)](LICENSE)

[核心亮点](#核心亮点) · [快速上手](#快速上手) · [工作台操作](#工作台操作) · [连接与数据位置](#连接与数据位置) · [使用许可](#使用许可)

</div>

## 核心亮点

- **选题时能找回原话。** 把访谈和观察记录合成一个批次，工作台会列出候选选题、推荐依据和对应原句，你核对后再定题。
- **改稿之后还能追溯。** 审核绑定具体版本，导出时能选定稿，也能从稿件回到原始素材。
- **看运营和做内容各有入口。** 首页按平台看日报和趋势，每日工作流里看批次进度，采集渠道页配置来源和频率。

![从两份原始材料核对同一个选题的依据](assets/evidence.png)

## 快速上手

使用 Python 3.12，在仓库根目录执行：

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765

Windows 通过 py -3.12 -m venv .venv 创建环境，后续使用 .venv\Scripts\python.exe 替代 .venv/bin/python。打开 http://127.0.0.1:8765 。首次打开进入账号运营总览。点击「初始化工作台」填写项目名、默认受众和文体，选择数据目录，并按需设置首个来源。本地 SQLite 已自动初始化。外部来源、模型、转写与 Notion 均可稍后添加。已有运行或项目配置的工作区直接打开总览。

每次运行独立保存字段映射、清洗阈值、关键词、原始材料、阶段结果与稿件版本。清洗在本次导入范围内按规范化正文去重；分析优先摘录包含指定关键词的句子。未命中时采用第一句。每次运行选定一个候选制作草稿。

## 工作台操作

账号总览显示所选日期的账号指标、各平台表现及七天趋势。账号和来源由使用者自行添加。每日工作流用于查看批次进度、待确认选题与待审稿件。采集动态从启用批次记录后开始累计。素材库支持搜索、清洗对照和跨来源选取；选中同一项目的素材后可合并为新的分析批次。分析与选题可核对候选理由与原文，稿件库可查看当前审核状态，打开批次后可编辑和导出指定版本。以上操作在本机和自托管部署共用同一套页面。

1. 打开项目与来源设置，可建立新项目，设置最短正文、关键词、跨素材主题、受众及稿件形式。项目默认值用于之后的运行。单次导入可调整字段和清洗规则，先点预览，再创建运行。
2. 来源支持 HTTPS RSS/Atom、静态网页、JSON API 与 Notion 数据库。先试采再保存。刷新间隔 0 表示手动；服务运行时定期检查来源。相同来源按条目标识和内容指纹识别新增、变更及重复，失败保留旧数据。网页抽取 HTTPS 响应里的可读文字；需要浏览器执行的动态内容可手动导入。JSON API 使用 GET，可配置记录路径、字段、稳定 ID、同源下一页 URL 字段和 Bearer 授权（环境变量名以 DM_API_TOKEN_ 开头），每轮最多 10 页、2000 条。RSS 历史覆盖取决于来源的保留窗口。
3. 进入运行后依次清洗、提取证据和选题。跨素材分析按自选主题及重复线索推荐选题，列出每个建议的支持材料。保留的运行可在刷新或重启服务后继续。重新计算会从该批原始材料建立新运行。
4. 生产阶段可用本地摘录或模型生成。编辑、保存、审核并导出；同题衍生稿独立保留各自的历史版本。审核决定绑定稿件版本。模型请求会把选题、选定证据、受众和格式发送到配置的服务。

文件选择器也支持 PDF 和常见音视频。PDF 按页提取（10 MB、200 页以内）；启用 OCR 并安装 Poppler、Tesseract 后，可识别最多 20 张没有文本层的扫描页。音视频先通过 FFmpeg 提取音轨，再发送到用户配置的转写服务（上传上限 25 MB）。提取结果可以在导入前核对，工作区保存从文件得到的文本及文件名，原始二进制文件由用户保管。

## 连接与数据位置

在启动进程的环境中设置可选变量，凭据不随工作区备份导出。

本地也可复制 .dm.env.example 为 .dm.env，填好要启用的项目，再在上述启动命令末尾加 --env-file .dm.env。文件已加入 Git 忽略规则。在「配置 → 数据存储」可以迁移全部项目到新目录。服务会复制并校验 SQLite，随后将读写切换到新位置，旧库保留。启动目录中的 storage-settings.sqlite3 记录当前位置，重启时请沿用相同的 DM_DATA_DIR。

在「配置 → AI 与多媒体引擎」可查看实际 SQLite 目录、执行小额模型生成测试、验证指定 Notion 库的读取权限，以及检查转写和 OCR 的本地准备情况。转写与 OCR 的最终结果通过上传短样本验证。Notion 写入权限和结果通过快照回读验证。

| 环境变量 | 用途 |
| --- | --- |
| DM_DATA_DIR | 初始数据目录和存储位置配置所在目录，默认 .dm-data |
| DM_MODEL_BASE_URL | Chat Completions 接口根地址，通常以 /v1 结尾 |
| DM_MODEL_NAME、DM_MODEL_KEY | 模型标识及可选密钥 |
| DM_NOTION_TOKEN | 用户自建 Notion integration 的凭据；在 Notion 将相应数据库授权给它 |
| DM_ASR_BASE_URL | 兼容 OpenAI 音频转写的根地址，通常以 /v1 结尾 |
| DM_ASR_MODEL、DM_ASR_KEY | 转写模型标识及可选密钥 |
| DM_OCR_ENABLED、DM_OCR_LANG | 将启用值设为 1；语言默认 eng，容器内也可选择 chi_sim 或 chi_sim+eng |
| DM_RSS_TRUSTED_HOSTS | 可选。使用本机 DNS 代理时，可显式列出信任的订阅主机名，以逗号分隔。默认仅接受解析至公网的 HTTPS 地址；连接不跟随重定向 |

Notion 使用 2022-06-28 数据库 API。输入连接读取用户选择的标题或富文本属性；输出连接仅需目标库的标题属性。每次送达保存一个可回读的 JSON 业务快照，包括原始记录、分析、选题、稿件和版本。项目设置指定目标库后，每次运行变更自动排队；也可以按运行手动保存。界面提供回执和重试。外部写入结果未知时优先回读核实，人工改动过的快照会报告冲突。单个快照上限约 350 KB；更大的业务数据可使用 SQLite 备份。Notion 作为远端版本存档，本地库始终保存运行状态和完整工作数据。

可在项目设置用快照页面 ID 恢复出独立运行，继续选题、审稿或导出。恢复默认仅写本地，原页面保留。

仅以单个服务进程监听本机回环地址。定时采集与 Notion 同步需服务持续运行。同一实例内的所有项目共享服务环境变量和本机凭据，项目用于分类，不能充当多用户访问边界。

## 云端自托管

在装有 Docker Compose 2.24.0+ 的主机，在仓库根目录执行。选用连接器时，可在仓库根目录创建未纳入 Git 的 .dm.env 写入相应变量。

    docker compose up --build -d --wait

服务在主机的 127.0.0.1:8765 可用，默认数据保存在 Compose 的 workbench 卷，挂载到容器 /data。容器内另选目录时，请先将目标目录挂载为持久卷并确保容器用户有写权限；页面填写容器内路径。远程浏览器可通过云主机的 SSH 端口转发访问：

    ssh -N -L 8765:127.0.0.1:8765 user@server

然后打开本机 http://127.0.0.1:8765。SSH 承担访问控制。停止应使用 docker compose down；删除卷会同时清除工作区数据。重启后原稿件、进度和来源配置继续使用同一卷。

## 备份、恢复与检查

    python backup.py export workbench-backup.sqlite3
    python backup.py restore workbench-backup.sqlite3 --directory restored-workspace
    python -m unittest discover -s tests -v

浏览器验收使用独立的临时数据库，包含桌面与手机宽度的工作流。在测试环境安装 Playwright 和 Chrome 后运行：

    python -m pip install playwright
    python -m playwright install chrome
    python tests/browser_acceptance.py

恢复到新目录，校验后设置 DM_DATA_DIR 指向恢复目录并启动。无需在原数据上执行覆盖操作。备份包含业务材料、稿件及来源地址，请自行保管。凭据在目标机器上单独配置。

### 第三方连接

在「配置 → 第三方连接」为当前项目分别保存一个日报来源和一个稿件接收端。接口使用 HTTPS，可选 Bearer 鉴权。请在服务环境中设置形如 `DM_API_TOKEN_ANALYTICS` 的变量，在页面填写变量名。

- 日报来源接受 GET。记录数组可放在顶层或指定路径（例如 `data.items`）。每条记录需要映射平台、账号、日期、期末粉丝、当日阅读或播放、互动、发布七个字段。支持同源下一页 URL（最多 10 页、合计 2000 条）。预览后手动导入，同项目、平台、账号、日期的日报会更新。确认时若读取内容变化，需要重新预览。
- 稿件接收端接受 JSON POST。在最新稿件审核通过后，展开「发送到第三方接口」，核对地址和正文后确认发送。负载包含 `schema_version`（1）、`event`（draft.approved）、project_id、run_id、version、title、markdown。2xx 表示接收端已受理。接收端应按 `Idempotency-Key` 去重，并在数据持久化后返回成功状态；网络超时后使用同一键重试。

## 分发给其他使用者

运行 `python tools/build_release.py` 生成 `dist/DM-Content-Workbench.zip`。交付此压缩包即可，每位使用者需准备 Python 3.12，解压后按「快速上手」安装依赖并创建自己的项目。首次安装依赖需要可访问 Python 包源。打包清单仅收录程序、示例配置、说明和测试，不收录本机数据库、备份、截图、研究文档或实际连接配置。

平台标志采用 Simple Icons 13.21.0 的图形（CC0），用于标识相应服务；品牌商标归各自权利人所有。

## 使用许可

本项目采用 **[CC BY-NC-SA 4.0](LICENSE)**（知识共享 署名-非商业性使用-相同方式共享 4.0 国际）协议，仅供个人学习与非商业用途使用，**禁止商业使用或打包售卖**；修改与再分发需保留署名并沿用相同协议。详见 [LICENSE](LICENSE) 和 [第三方声明](NOTICE)。

## 关于作者

作者：**[dylanma8232-art](https://github.com/dylanma8232-art)** · **[返回个人主页查看更多项目 →](https://github.com/dylanma8232-art)**
