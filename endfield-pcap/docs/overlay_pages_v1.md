# 网页悬浮窗本地测试版

版本：`0.1.0-preview.6`，基于客户端源码 `0261088` 后的本地改动。本轮没有发布网站、更新清单或正式客户端。

本轮完善只读通用数据接口，并提供规则保留在 HTML 中的狼血示例，不提供网站或插件编辑器。具体字段、分页、可用性见 [数据接口说明](overlay_data_api_v1.md)。狼血示例要求同版证明和明确清零锚点，详见 `overlay_wolfblood_example.md`；历史版本结果不能替代本版源码与冻结包验证。本轮未重新运行最终冻结包公网 HTTPS 检查。

## 使用入口

- `C:\path\to\endfield-suite\启动悬浮窗预览.cmd`：独立窗口与设置预览，不启动采集和上传。没有实际数据时显示等待状态。
- `C:\path\to\endfield-suite\启动悬浮窗实战测试.cmd`：从当前源码启动统一客户端的本地测试，使用独立自定义窗口配置，保留原 DPS / rDPS 窗口。本地测试关闭自动上传，不启动第二个采集器；先退出已经运行的正式客户端，再自行启动测试与游戏。
- 预览构建与实战测试状态位于 `C:\path\to\local-test\endfield-overlays-20261018`；本轮接口验收位于 `C:\path\to\local-test\endfield-overlays-20261019-api`。用户原有自定义 HTML 不会被复制删除或覆盖。

设置左侧选择基础 DPS、排轴指导或独立页面；右侧调整窗口。每个页面分别保存尺寸、位置、透明度、启用、显示、锁定和穿透。窗口失去交互时用“找回窗口”：恢复显示、取消锁定与穿透、恢复透明度并回到当前屏幕。

首版静音，切出游戏后自定义窗口继续留在桌面，不提供整套配置导入导出。现有排轴指导条的计时、场景显示策略与原统计算法没有被新页面接管。

## 内置页面

`src/endfield_pcap/overlay_examples/effects-monitor.html` 是可直接编辑加载的独立 HTML：

- 每窗选择本队、指定角色或明确目标，目标列表包含已观测到的怪物，可以分别选择同名不同实例。
- 不按 rDPS 白名单筛选。未选择效果 ID 时，默认接收符合目标且官方 `hasIcon=true` 的已观测效果；切换“全部观测”后才接收该目标范围的全部效果资料。
- 提供 3422 项当前所选官方资源中的效果身份目录。本轮补入 14 个精确图像来源，按 typed `_spritePath` 与固定资源索引的准确键对应，不按图片或名称相似度匹配。罗丹原日志中的 35 项游戏图标效果分别对应 34 项图像和 1 项技能按钮进度；2026-10-19 原日志中的 24 项分别对应 22 项图像和 2 项技能按钮进度，两份回放均没有真实缺图。
- 默认显示游戏配置了图标的效果，可切换全部观测。图标按目标分组，同一准确身份的多条观测折叠；观测条数移入悬浮详情，不再在卡片上当作数字角标，也不作为层数。
- 当前目录包含 2966 项中文配置说明，优先显示有明确来源的配置摘要；未确认专名的不会伪造官方名称。目标目录包含 445 项通过准确官方关系取得的名称，仍须与实际观测目标建立明确身份关联。准确 ID 在设置和检查器中可查。
- 仅官方图标配置中六个准确的正负显示组用于图标分组；不从 ID 名称、图像相似或属性符号猜分类，也不把 UI 显示组用于伤害／rDPS。
- 设置展开时保留已选效果的未观测占位；日常可隐藏未观测项或名称未知项。新增“隐藏状态未知项”和“日常隐藏已停用项”两个独立设置，前者默认关闭，后者默认开启；展开设置时可查看已停用项。
- 尚未关闭的记录分成 `active / disabled / unknown`：只有 `enabled=true` 标为“已确认生效”，`enabled=false` 标为“已停用”，没有明确布尔值则显示“状态未知”，不从尚未结束推断生效。停用不等于实例结束，结束后不能由迟到的启用更新重新打开。
- 未知层数和时长不推算；不会用组大小、技能次数、增强倍率或文案制造叠层和倒计时。官方配置只提供技能按钮进度显示的项目以“技能按钮进度”标记，不捏造百分比或改造成普通图标。
- 断连立即清空。重连先等待新数据，不复用旧会话效果。

`topic-inspector.html` 显示只读主题与准确筛选后的快照，包括无法确定目标的原始效果观察。较大的 JSON 在界面标明截断范围，图像数据以长度摘要显示，避免浏览器因全文图像字符串反复渲染而卡住。

## 开发 API

页面只有两个方法，没有文件、账号、上传、采集或窗口管理接口：

```javascript
const topics = await window.endfieldOverlay.getTopics();
const unsubscribe = window.endfieldOverlay.subscribe({
  topic: 'effects.actual.v1',
  filter: { target: 'all', effectIds: [], targetIds: [], onlyGameIcons: true }
}, snapshot => {
  // 每条消息完整替换本主题显示；unavailable 时必须清空旧值。
  if (snapshot.state !== 'ready' || snapshot.data === null) {
    clearDisplay();
    return;
  }
  render(snapshot.data);
});
window.addEventListener('pagehide', unsubscribe);
```

消息格式：

```json
{"topic":"effects.actual.v1","schemaVersion":1,"sequence":1,"receivedAtMs":0,"state":"unavailable","reason":"capture_disconnected","data":null}
```

先发送当前快照，再发送变化。序号只在宿主进程内有效，接收时间不是技能施放时间。多个窗口共享同一个原采集服务和效果状态生产者，正常变化按约 100ms 合并推送，断连不等待合并周期。同主题和相同规范化筛选只构建一次投影，各窗口收到独立数据；回调修改不能影响另一个窗口或生产者。页面和窗口不能触发重解析、采集或上传。

主题：

| 主题 | 内容 | 断连 |
|---|---|---|
| `effects.actual.v1` | 已观测效果、组原始资料及 `unresolvedObservations` | unavailable / null |
| `targets.current.v1` | 准确场景目标、对象和战斗身份关系 | unavailable / null |
| `capture.context.v1` | 连接、会话与代数 | unavailable / null |
| `effects.catalog.v1` | 所选官方资源的静态效果目录与去重图像字典 | 仍可用；不代表实时游戏状态 |
| `effects.definitions.v1` | 按准确来源关系检索、分页的静态定义和配置参数 | 仍可用；首次订阅异步加载 |
| `effects.operations.v1` | 原始操作头与白名单字段的有界观察流，报告序列、内容补全与遗漏 | unavailable / 清空 |

筛选 `target` 支持 `all / party / character`，`characterIds` 为准确角色模板，`targetIds` 为明确战斗实例。`controlled / enemy / boss` 自动分类在缺乏依据时返回 selection unavailable，不以领队、队伍顺序、目标名称或伤害猜测。用户仍能从场景目标中手动选择怪物／首领实例。

`effectIds` 支持原始准确 ID 或具有明确官方映射的 canonical ID，不接受名称相似匹配。`TEMPLATE_ID` 数字映射须有明确 `type=Buff` 和当前同源官方数字表的准确键；专用 `BattleActionCreateBuff → create_buff_action → details[].buff_num_id` 则保留独立的协议类型来源，不伪造原本不存在的 `TEMPLATE_ID.type`。泛数字、global 或 persistent 身份不能借用这个上下文。数字身份不会仅因数值相同就跨协议、跨类型或跨目标合并。

效果包含准确 `namespace / effectId / canonicalId / instanceId / targetId / sourceId`，没有证据则为 null。服务端与客户端、全局、持久、竞速效果分别保留来源身份空间。部分无运行时实例的资料只具有槽标识，不伪造实例 ID。

`label / description` 可由当前官方配置生成摘要，分别以 `labelSource / descriptionSource=official-config-summary` 标明来源，不冒充官方 Buff 专名。说明限制在 800 字符以内，只发给运行时准确匹配记录，静态目录只含短名称和来源，避免大消息。动态 Blackboard 参数没有观测值时保持未知，不能使用配置 fallback。

`hasIcon` 是准确配置元数据，未知为 null；`uiGroup / uiGroupUsesNumericPriority / classificationSource` 保留官方图标显示组的来源。`classificationSource=official-icon-priority-group` 仅表示配置中的 UI 分组，不是玩法正负属性或 rDPS 分摊依据。`iconPresentation=skill-button-progress` 表示准确官方配置要求技能按钮进度显示，只是显示类型，不能据此生成运行时进度值。

静态目录的图像放在 `data.icons` 字典中，键是完整 data URI 的 SHA256，值是完整 `data:image/...;base64,...`；目录每行使用 `iconKey` 引用，`icon` 为 null，不再逐行重复图像。3422 项完整目录消息约 1.37 MB，图像没有截断。运行时效果记录继续保留 inline `icon`，以兼容独立页面的直接显示。

API 的 `onlyGameIcons` 默认为 false，内置正常窗口默认传 true；检查器和“全部观测”传 false。它只缩小该订阅的效果投影，采集和存储不裁剪。`scopeEffectCount / gameIconFilteredCount / deliveredEffectCount` 统计当前目标及效果筛选范围内的观测记录，包含历史或上下文未知记录，不能当作当前生效数量或层数。

`coverage=observed-only, completeness=partial, absence=unknown` 必须保留。实际移除、快照不再列出、目标离场／死亡、实例重绑定、上下文失效分别记录，不互相替代。`state=observed-open` 只表示尚未明确关闭，当前启停状态由独立的 `enabled` 布尔值决定。

结束、启停和实体代数分别有独立门禁：`clientFinishExact / clientEnableStateExact / entityGenerationExact / clientActionFinishExact / clientKeywordVariablesExact`，本轮再增加原始操作序号的 `clientOperationSequenceExact`。每个门禁分别核对实际安装 DLL／metadata 指纹、能力字段和证据摘要；一个能力通过不能替另一个通过。启动时在对应 trace 旁写入 `trace.log.overlay-startup.json`，记录本进程实现摘要、实际游戏指纹、加载资源摘要和各门禁状态。原日志没有这份启动记录时，当前安装文件和回放门禁通过不能反推旧进程当时加载了什么。

客户端 Finish 已接通一个有同版原生证据的准确形状：实际 DLL 与 metadata 指纹匹配运行时能力清单，UID 带客户端 bit 61，`reduce_layer=1`、`force=true`，并有明确 `operation_owner_id` 目标。仅在当前采集／场景内按准确目标和 UID 找到已有客户端实例时，标为 `observed-closed / client-finish-confirmed`；`finishedAtMs` 记录已结束观察，`removedAtMs` 不被伪造成物理移除时间。CreateBuffAction 的操作代数属于施放者，不拿它当目标代数门槛。

准确结束包若没有当前可用的创建记录，仍进入 `unresolvedObservations`，不造效果身份；未知版本、其他形状或缺目标／UID时仍保留未知，不通过“只有一个候选”归属。能力状态可查 `lifecycleEvidence.clientFinishExact`。原生证明见 `docs/evidence/overlay_finish_native_20261018.json`。

启停只在 `clientEnableStateExact` 门禁通过后，按同一采集／场景内明确 `owner_id` 目标与客户端 bit 61 UID 更新已有实例的 `enabled`。`states` 是逐项更新，未列出的实例不被当作停用；找不到明确创建记录时保留未解决观察，不能仅凭 UID 唯一候选归属，也不能重开已结束实例。原生证明见 `docs/evidence/overlay_enable_native_20261019.json`。

单实体 `SC_RESET_BATTLE_STATUS` 只使明确 `battle_inst_id` 对应目标的旧记录失效，不再清空其他目标或整个采集上下文；明确目标缺失或字段冲突时保留未解决观察。`SC_BATTLE_GENERATION_CHANGE` 须通过 `entityGenerationExact`，并有实际观测到的 object → battle 关系；只有同一目标已知前后代数确实变化才使该目标旧记录失效，首次观测代数不能用来判断已有记录属于旧代。

## preview.4 通用接口变更

新增定义主题包含 3422 条当前资源的效果定义，其中 921 条有明确的角色、武器、装备、套装或敌人出生配置来源关系。敌人来源的 267 条包含 136 个直接出生 Buff 与明确派生关系；未提取的敌人技能资产保持缺失，不能声称覆盖所有敌人技能。来源配置不证明本场实际施放者，也不证明效果一定存在或生效。

运行值、字段来源、初始化赋值、静态配置和原始字段分别交付。未知、不支持、真实零值分别保留；`rate` 仅作为已证明的倍率变量，通用精确层数与剩余时间仍不支持。CreateBuffAction 的 End 请求不能当成实际结束；新增独立 FinishBuffAction 的 Execute 形状才可按准确目标与 UID 结束已有实例。已保留的结束记录不会被重复创建包复活；有限历史被淘汰后的 UID 不保证永久去重。

实际主题在 1500000 字节消息预算内分页，开放记录优先，明确交付、匹配和省略数量。页间没有事务一致性，不能把不同更新的页伪装成同一时刻全量状态。完整订阅请求超出 32768 字符时，宿主立即返回 `overlay-request-limit`；不再等超时后误报连接问题。页面收到不可用快照应清空旧运行数据。

`data-api-minimal.html` 只有顶部手工配置与只读展示，没有表单或编辑器；默认按准确队伍关系订阅，无法确认目标时等待。它不从静态最大层数、内部倍率、实例数量或未证明单位生成叠层和倒计时。

## 页面边界

- 本地入口必须为现有 HTML，同目录及其子目录资源在真实路径解析后仍须留在入口根内；目录 junction／软链接不能越界。
- 远程入口为标准端口的公网 HTTPS。采用固定已验证公网 IP 与 TLS/SNI 校验，拒绝重定向和私网目标。首版只支持静态同源资源，不支持外站 CDN、后端接口或普通网页中的任意网络请求。
- 两类页面都通过相同的受限自有 scheme 与只读桥运行。每实例和精确入口独立持久存储；重载保留自己的 localStorage，另一个窗口不能读取它。
- 保留 Chromium sandbox；仅在主帧安装受限桥，拒绝任意导航、子帧、弹窗、下载、权限和后台服务访问。页面不能调用 native 设置管理器。
- 最多 24 个窗口，每窗 32 个订阅。窗口忙时只保留最新替换快照，避免积压。过大消息明确清空并返回 `window-payload-limit`，应缩小筛选范围，不能继续显示旧资料。

## 采集补项和剩余验收

新增接出：角色背包／自身场景／进视野／重置的服务端效果列表、目标离场、持久效果、全局效果、竞速副本修改与重连资料、client Add / Finish／启停原始参数、实体代数和采集会话开始／结束。单实体重置与实体代数变更按各自准确身份边界处理。保留 protobuf 的 0、false 和大整数身份，不改原伤害/rDPS/官方计时解析。

本轮资源门禁发现安装游戏已有热更新，已按 SOP 重提取 `run_20261018_212131`，重新生成并同步当前同源 StringPool、完整 typed 根与双仓来源凭据。770 张表及数字映射完全相同；3395 个 Buff 语义相同，2928 个技能中仅别礼连携新增明确事件分支，伤害索引、参数及公式未变。独立资源门禁与 71 项相关测试通过，详见 `docs/evidence/overlay_resource_refresh_20261018.json`。

仍缺直接证明的内容包括服务端时长单位和时基、通用层数、部分服务端来源／目标关联、当前操控角色和自动敌方／首领分类。原生默认分支的条件关系及 IFix 局限见 `docs/evidence/overlay_effect_native_20261018.json`；这些缺项没有被填成默认值。本版不能声称已经完成“游戏所有效果”的实战覆盖验收。

验证分级：单元与真实 protobuf 合成回归；固定 NDJSON 与 trace 回放；实际 QtWebEngine 源码页面；最终冻结测试包。模拟数据和旧日志回放均不代表新代码已通过现场实战。最后仍需用户在新采集版实际测试进入场景已有效果、双方新增／刷新／移除、暂停、重开、切场及重连，逐项核对游戏显示和准确实例数据。

本次 `preview.3` 修正回放了第一次罗丹本地测试与 2026-10-19 采集的原日志；它们不是本轮修正后的新版新实战。394 项 Python 回归、9 项 JavaScript 检查和源码／冻结 Qt 的各 11 项本地检查已通过；Qt 检查使用模拟事件和实际页面，没有启动游戏采集或上传。双日志中的 44／593 条启停更新均准确应用，单目标重置未修改其他目标，两场退出均清空。原日志没有代际变化与关闭后晚到启用样本，这些边界由回归测试覆盖，不能冒充实战验证。

两份原日志的图像与配置说明覆盖见 `docs/evidence/overlay_effect_assets_20261019.json`。启停、单实体 RESET 与代数处理仍须在新版本实际采集中逐项核对；未触发的回放分支不能算作实战验证。

## preview.1 已完成的验收历史

以下只记录 `0.1.0-preview.1`，不能外推为后续版本的构包结果：当时当前资源下的相关客户端／设置／采集／计时／打包检查 314 项通过，资源流程另有 71 项通过。该冻结 EXE 的本地页面和桥接共 10 项检查通过，6 个实际 PYZ 模块及 HTML、数据均与源码一致。公网 HTTPS 入口在源码 Qt 实际运行中通过；该最终 EXE 的公网复查启动被自动审批拒绝，原因仅为 `blocked by policy`，没有绕过，也不计为最终包公网验收通过。

## preview.2 已完成的验收历史

`0.1.0-preview.2` 已完成 353 项 Python 回归、4 项 JavaScript 检查及冻结包实际 QtWebEngine 本地页面的 10 项检查，冻结模块／资源与当时源码一致，压缩包 CRC 与摘要已核对。该历史不包含最终冻结包公网 HTTPS 验证，也不能替代 `preview.3` 验收。详见 `docs/evidence/overlay_rodan_preview_v2_20261018.json`。

当时修正使用第一次罗丹本地测试的固定日志回放，范围与结果见 `docs/overlay_rodan_akedata_review_20261018.md`；用户没有为这些修正重新打一场。

## 窗口诊断与恢复

独立悬浮窗设置中的“错误日志”可查看、复制此窗口本次运行的脱敏诊断。记录包含 JavaScript 错误／警告、页面加载失败、渲染进程退出和恢复动作；不记录源入口地址、页面正文、网络包或订阅数据。消息中的 URL、文件路径、常见凭据与长标识会被隐藏，但用户仍应在分享日志前检查自定义 HTML 输出。每窗保留最近 100 条，JavaScript 日志每分钟最多 30 条，超额有明确计数。磁盘日志保存在窗口浏览器状态目录的 `diagnostics/overlay-windows.jsonl`，最多 512 KiB，另保留一份同限额旧日志；写入失败不影响采集或窗口运行。

渲染异常会先取消旧页面订阅、隐藏旧内容并显示原生错误提示，按 2／5／10 秒间隔最多自动重试三次。页面连续稳定 60 秒后恢复重试预算；达到上限后须手动重载。隐藏、停用、删除窗口、切换入口及退出会取消旧的待恢复定时器。隐藏期间发生异常不会自动重试。普通加载失败只提示并提供手动重载，不无限请求远程地址。恢复仅重建该页面连接，从 broker 重新读取当前状态，不改变其他窗口、采集或上传模块；断连时仍收到不可用状态和空数据。

这不是内置 HTML 编辑器，也不恢复排轴和指导条。窗口恢复的真实 Qt 测试包含杀死测试进程自己创建的渲染子进程、重新订阅、重试上限和停用清理，不能代替最终冻结包或游戏实战验收。

## 主界面中的统一设置

统一客户端左侧“悬浮窗”页面管理基础 DPS 和所有独立 HTML 窗口。切换页面不清空战斗记录、勾选状态或后台监听；原浮窗设置按钮会恢复并定位到主界面的设置页。

采集运行时通过仅同用户可连接、每次启动独立授权的本机通道即时修改。单独打开上传器时可离线保存，下次启动生效；已运行的其他宿主或受管连接中断时禁止离线覆盖配置。此控制通道不向用户 HTML 或外部浏览器开放，HTML 数据接口仍为只读。

原生页面提供来源、显示开关、透明度、位置尺寸、锁定穿透、重载、找回与错误日志，不包含 HTML 编辑器；排轴保持停用。
