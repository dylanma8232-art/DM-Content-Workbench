# DM 发布助手

在 Chrome 106 或更新版本打开 `chrome://extensions`，开启开发者模式，选择「加载已解压的扩展程序」并选中本目录。刷新本机工作台，点击稿件对应账号的「工作台辅助填写」。支持的平台 ID 为 `bilibili` 和 `xiaohongshu`。

在新打开的创作页登录并核对账号，选择视频或图文上传模式，打开工具栏中的 DM 发布助手。检查文案与素材后点击「已核对当前账号，填入草稿」。保持弹窗打开直到显示结果。平台可能在选中文件时立即开始上传。若文案输入框在上传后才出现，请等待表单加载，再取消勾选素材并填入文案。检查平台预览后由用户完成发布。

## 工作台接口

顶层本机 HTTP 页面可通过 `document.documentElement.dataset.dmPublishExtension === '1'` 检测助手。按钮点击时调用 `window.postMessage({type: 'dm-publish-open', project, post, target}, location.origin)`，三个 ID 均为正安全整数。助手从浏览器验证过的发送来源构建 `GET /api/projects/{project}/publishing`，按目标平台打开固定创作页。媒体通过同一接口下的 `/assets/{id}` 读取。任务绑定到该标签页，有效期 30 分钟。关闭标签页或重启浏览器将清除对应任务。

扩展对 `localhost` 和 `127.0.0.1` 的 HTTP 页面启用桥接，通过用户打开弹窗时授予的 `activeTab` 访问创作页。账号名称来自工作台，实际登录账号需要用户核对。每次支持合计 64 MiB 以内的图片或视频。站点需提供可识别的顶层编辑控件以及唯一且接受所选文件的上传控件。平台改版、嵌入式上传页、登录验证或素材不匹配时，可根据提示手动补填或上传。

## 平台作品采集

在工作台运行采集任务后，若工作台提示使用浏览器，请点击打开平台页。在该标签页完成平台要求的登录并返回原任务地址，打开工具栏助手，点击「采集此页作品并提交」。保持弹窗打开。助手先读取列表并按平台作品 ID 去重、滚动或翻页，再逐篇读取详情和作品配图地址。服务端继续保存原图、识别文字、提取视频内容并执行可用的模型解析；素材库显示实际覆盖范围与缺项。弹窗显示工作台返回的新增、更新和重复数。登录提示、空搜索结果、只有图片的卡片和页面改版均可能导致 0 条。

支持 Instagram、TikTok、抖音、小红书、哔哩哔哩和 X 的账号及关键词搜索。Instagram 使用 `/explore/search/keyword/?q=…` 搜索关键词。`/explore/tags/…` 是独立的标签页，任务不使用该路由。扩展使用工具栏的 `activeTab`，逐篇读取前会请求当前平台的可选访问权限；不读取 Cookie。更新至 1.3.0 后须重新加载扩展并刷新工作台。账号页使用账号标识，搜索页保留原搜索词和筛选条件。

### 采集接口与打包

1. 顶层本机工作台可用 `document.documentElement.dataset.dmCollectExtension === '1'` 检测助手。按钮点击处理程序发送 `window.postMessage({type:'dm-collect-open', project, source}, location.origin)`，两个 ID 均为正安全整数。
2. 助手从发送来源请求 `GET /api/projects/{project}/collection-tasks/{source}`。使用返回的 `{id, platform, mode, query, limit, url, attempt_token}`。`mode` 为 `account` 或 `topic`，条数为 1 至 50。打开与任务相符的官方 HTTPS 地址，并把该次尝试绑定到新标签页，有效期 30 分钟。
3. 助手向原来源的 `POST /api/projects/{project}/collection-tasks/{source}/results` 提交 JSON `{items:[{external_id,title,body,url,author}],attempt_token}`。作品地址移除跟踪参数，作者不可见时为空字符串。空结果发送 `items:[]` 并显示 0 条。令牌取自打开任务时的快照，过期尝试由工作台拒绝。接口返回 JSON，其中 `message` 用于展示处理结果。
4. 在原发布扩展运行文件外，发布包需要包含 `collection.mjs`、`collection-popup.mjs` 、`collect-page.mjs` 和 `collect-detail.mjs`。源码发行包可一并包含 `collection.test.mjs`。Chrome 运行包使用同一目录下的相对导入。

`collect-page.mjs` 单独导出无外部依赖的 `collectPage(task, expectedUrl, knownIds, advance = false)`，返回 `{items, next}`。调用方先核验官方路由与任务目标，再传入 `{platform,mode,query,url,limit}`、当前完整地址及已采集 ID 列表。调用会读取和滚动当前文档。`next` 是待核验的下一页地址或 `null`。B 站账号投稿列表连续无新增时，传入 `advance = true` 可触发列表的下一页按钮。

## 抖音日报

刷新本机工作台后，点击「连接」进入扩展确认页。页内显示目标工作台地址和项目 ID。登录创作者中心，点击授权按钮，核对官方首页显示的昵称和抖音号后确认绑定。每个 Chrome 配置文件支持一个绑定。确认后立即尝试回填一次。在工作台开启每日回填后，Chrome 于上海时间中午 12 点读取昨日数据。启动或唤醒时已过中午且缺少昨日日报，会尝试补读。若中午后已取得完整日报，但本机工作台离线、超时、限流或暂时出错，扩展会在绑定记录中暂存该份日报，5 分钟后重试提交，之后逐步延长至每小时一次，最晚尝试到上海时间 23:59。自动补交直接使用已核对的数据。关闭定时会取消补交，成功提交或解除绑定会清除暂存。采集失败时可在工作台查看原因并手动重试。

页面检测 `document.documentElement.dataset.dmDouyinDailyExtension === '1'`。通过 `window.postMessage({type:'dm-douyin-daily', action, project, requestId}, location.origin)` 发起请求。`project` 为正安全整数，`requestId` 为调用方生成的唯一字符串。响应为 `{type:'dm-douyin-daily-result', action, project, requestId, ok, status?, error?}`。监听方核对 `source`、`origin` 和请求标识。成功响应的 `status` 为 `{connected, enabled, account, platform_uid, day, message}`。`day` 为最近成功提交的日期，尚无成功记录时为空串。

| action | 行为 |
| --- | --- |
| `connect` | 打开扩展确认页，待授权时返回 `connected:false`。轮询 `status` 可取得绑定和首次回填结果。 |
| `status` | 查询当前工作台来源及项目的状态，支持轮询。 |
| `retry` | 立即尝试读取昨日数据，返回启动状态。轮询至 `message` 显示成功或错误。 |
| `enable` | 开启每日定时；当天已过中午且尚缺昨日数据时，立即尝试回填。 |
| `disable` | 关闭每日定时，保留绑定与手动重试能力。 |
| `disconnect` | 解除当前项目绑定或取消待授权请求。 |

除 `status` 外，调用从点击事件发起。扩展固定绑定发起时的 HTTP loopback origin（含端口）与项目 ID。每次读取前后都从 `/creator-micro/home` 核对身份，在 `/creator-micro/data-center/operation` 分别点击作品数据和粉丝数据的「昨天」。两个分区的统计周期与日历范围均匹配上海昨日，且六项完整整数连续两次相同后，扩展优先复用绑定来源的工作台标签页，无可用标签时临时创建非活动标签并于提交后关闭。顶层页面的隔离环境核对来源后，通过同源 `POST /api/projects/{project}/douyin-daily` 提交 `{account,platform_uid,day,views,likes,comments,shares,followers,published}`。运行包需加入 `douyin-daily.mjs`、`douyin-page.mjs`、`douyin-connect.html`、`douyin-connect.mjs`，源码包另加 `douyin-daily.test.mjs`。

## 本地验证

使用 Node.js 22 执行 `node --test tools/publish-extension/*.test.mjs`。测试使用模拟的工作台响应与创作页控件，覆盖来源和路径约束、稿件选择、文件字节传递、纯文本填写和缺失控件提示。
