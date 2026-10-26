# HTML 宿主只读数据接口 v1

这是供自写 HTML 使用的数据接口与宿主。页面自行决定如何展示数据；接口没有网站搭建、插件编辑、布局编辑或采集控制能力。

客户端 HTML 宿主向顶层页面注入 `window.endfieldOverlay`，只提供两个方法：

```js
const topics = await window.endfieldOverlay.getTopics();
const unsubscribe = window.endfieldOverlay.subscribe(
  {topic: 'effects.actual.v1', filter: {target: 'party', effectIds: [], onlyGameIcons: false}},
  envelope => { /* 每次回调都是该订阅当前的完整投影快照 */ }
);
// 页面结束或更换订阅时：unsubscribe();
```

`getTopics()` 返回 Promise，描述主题、版本、只读属性和能力；`subscribe()` 返回取消函数。订阅错误也通过回调中的不可用快照报告。不要调用内部 Qt 通道、内部 broker 方法或采集服务；页面无需访问网络、文件、进程或游戏接口。宿主的连接机制由客户端管理。

## 七个主题

| 主题 | 内容 | 离线行为 |
|---|---|---|
| `effects.actual.v1` | 明确观测到的效果实例、运行值和字段可用性 | 无新鲜采集上下文时不可用；页面清空旧记录 |
| `targets.current.v1` | 明确观测到的场景目标及受控目标关系 | 无新鲜上下文时不可用 |
| `capture.context.v1` | 采集连接、会话及场景代次 | 不可用表示不能延续旧场景解释 |
| `effects.catalog.v1` | 本机资源中的效果 ID、配置摘要和图像字典 | 可查，不要求实战出现 |
| `effects.definitions.v1` | 可检索、分页的官方静态定义、角色/武器/装备/套装来源关系及参数证据 | 可查，不要求实战出现；首次订阅异步加载 |
| `effects.operations.v1` | 有界客户端操作观察流；原始操作头与明确的效果字段、顺序和缺口信息 | 断连或上下文失效即不可用并清空 |
| `game.events.v1` | 有界游戏观察事件：技能、队伍、配装、属性、Buff、计时及已解析的运行参数 | 断连或上下文失效即不可用并清空 |

静态定义的大文件仅在首次定义订阅时加载，由进程内共享缓存复用；仅使用 DPS 或实际效果订阅不会主动解析它。定义加载失败不影响原采集或其他主题。不要把完整定义当成初始全量广播。

`target: 'enemy'/'boss'` 可以订阅有明确敌人/首领身份的实际观测。静态定义来源目前仅覆盖角色、武器、装备、套装和敌人出生配置五类；敌人来源只从 `EnemyTable.bornBuffs` 及其明确 typed 创建关系建立，未覆盖全部敌人技能或缺失的 AI/EnemyData 资产。当前 3422 条是本次本机资源 ID 集合的定义覆盖，不代表全游戏 Buff 语义、所有来源关系、层数或时间语义都已覆盖。

## 通用快照与 ID

```js
{
  topic: 'effects.actual.v1', schemaVersion: 1,
  sequence: 12, receivedAtMs: 1790000000000,
  state: 'ready', reason: null, data: {/* topic-specific */}
}
```

`state` 为 `ready` 或 `unavailable`；只有 `ready` 的 `data` 可使用。定义的异步 loading 使用 `state: 'unavailable', reason: 'definitions_loading'`，随后同一订阅收到 ready；不要将 loading 当成空结果或零层。加载失败保持 unavailable，并附原因。连接失效、场景切换或数据过期后，应立即清除旧运行观测，等待新快照，不能继续展示旧场景的剩余时间。

效果、目标等投影中的 ID 一律按字符串处理，包括角色、目标、效果实例、slot 和数字 Buff ID。不要转换为 JavaScript Number，不要用浮点数比较 uint64。`canonicalId` 是有明确官方映射时的规范 ID；`effectId` 保留当前效果身份。未知映射保持未知，不能按 ID 前缀、名字相似或角色阵容猜来源。`game.events.v1` 的原始规范化 `payload` 保留各消息字段形状；其中超出 JavaScript 安全整数范围的整数统一用十进制字符串传递，原本的 protobuf int64 字符串也不转成 Number。不要根据字符串或数字类型自行猜字段含义。

实际主题可筛选 `target: 'all' | 'party' | 'controlled' | 'enemy' | 'boss' | 'character'`、`targetIds`、`characterIds`、`effectIds`、`hideUnknown` 和 `onlyGameIcons`。`target: 'character'` 使用准确 `characterIds`；明确 `targetIds` 优先。受控目标或敌我关系未确认时，检查 `data.selectionState` 与 `selectionReason`，不能任取一个目标代替。身份数组最多 4096 项，字符串最长 256 字符。

所有筛选还共同受桥请求总长度限制：单个序列化 JSON 请求最多 32768 字符（ASCII 内容约 32 KiB，包含操作、订阅 ID 和筛选包装）。数组项数合规并不保证整个请求能通过；应限制总 ID 数量/长度并预留包装空间，超限需要缩小筛选，不能把请求超时当作空数据。

实际桥请求结构为 `{op:'subscribe', subscriptionId:'s…', topic, options:{filter}, requestId:'r…'}`。最小示例在提交实际订阅前序列化这一完整结构，为两个由宿主产生的编号各预留 32 位数字，再按 JavaScript 字符串长度检查 32768 上限。不能只计算 `effectIds.length` 或 `{topic,filter}` 长度。超限时立即提示缩小查询/ID 列表，不提交订阅；例如宽泛 `query: 'buff'` 即使结果少于 4096，也可能超过请求预算。桥回调的 unavailable `reason` 会原样展示为订阅不可用原因，不统一解释为采集断开。

宿主公共 API 也检查完整请求长度：订阅请求超限时立即通过原订阅回调返回 `state: 'unavailable', reason: 'overlay-request-limit'`，无需等待连接超时。示例自己的预检提供更直接的“缩小查询/ID 列表”提示；其他页面也应保留并展示宿主给出的错误原因。

`effects.actual.v1` 另外支持 `filter.limit: 1–4096`（默认不加条数上限）和 `offset: 0–1000000`（默认 0）。结果仍受消息预算限制，按开放观测优先、每类原插入顺序交付。实际主题返回 `offset/limit/matchingEffectCount/deliveredEffectCount/hasMore/nextOffset`；只有本页快照，不提供跨页事务一致性，sequence 变化后不可把各页拼成同一时刻全量状态。`targets.current/capture.context` 不因此获得分页；定义主题继续使用自己的默认 25/最大 100。

实际消息的 `projectionReason` 为 null、`pagination` 或 `payload_budget`，`projectionByteLimit` 是 1500000。单条记录也可能无法放入预算，此时 hasMore 仍为 true、nextOffset 为 null；应缩小目标/效果范围，不得把空页解释成没有效果。`projectionOmittedOpenCount/projectionOmittedClosedCount` 是全部匹配记录中本页未交付的数量，含页前/页后；Closed 沿历史分类也包含 context-unknown。`storageOmittedOpenCount/storageOmittedClosedCount` 是当前采集上下文的全局容量淘汰数量，不能当成这个筛选范围的精确缺失数量；旧 `omittedOpenCount/omittedClosedCount` 为这两类相应合计。`omittedStackingGroupCount/omittedUnresolvedObservationCount` 单独报告因消息预算没有交付的分组/未解析观测。

## 定义查询、分页与来源

```js
const stop = window.endfieldOverlay.subscribe({
  topic: 'effects.definitions.v1',
  filter: {query: '狼之绯', limit: 25, offset: 0}
}, envelope => {
  if (envelope.state !== 'ready') return; // definitions_loading 时继续等待
  const {definitions, icons, total, hasMore, nextOffset} = envelope.data;
  // 保存本页 canonicalId；hasMore 时以 nextOffset 请求下一页。
});
```

`query` 最长 256 字符，检索 ID、配置摘要和已明确关联的来源名称/技能文案；不会从“狼血”等文案字词创造 Buff 专名。可以另用 `sourceTypes`、`sourceIds`、`effectIds` 精确筛选，来源类型与来源 ID 必须匹配同一条显式关系。按当前实现，`sourceTypes/sourceIds` 各最多 256 项，`effectIds` 最多 4096 项，且受上面的请求总长度限制。来源类型是 `character`、`weapon`、`equipment`、`equipment-set` 和 `enemy`；单件装备与套装分开。`enemy` 的 `sourceScope: 'born-buff-configuration'` 明确限定出生配置来源。

`limit` 为 1–100，默认 25；`offset` 为 0–1000000，默认 0。返回 `definitions/total/offset/limit/hasMore/nextOffset`。消息预算可能使一页短于 limit；必须按 `nextOffset` 继续，不能自己加 limit。`hasMore: true` 但没有递增的 nextOffset 时，结果未完整交付，需缩小筛选；检查 `paginationReason`。定义消息内 `icons` 仅包含本页用到的内联图像，按 `iconKey` 取用。

普通定义页省略 `details`，单个准确 `effectIds` 查询可返回路径/各级参数等详情；详情仍受消息预算限制。多来源必须保留；列表可能仅带前 32 条来源关系，同时给出 `sourceRelationsTotal/sourceRelationsTruncated`，不得假装来源完整。来源关系的 `relationKind: 'official-static-definition'` 只证明官方角色/武器/装备到技能及 Buff 的配置关系，**不证明本场谁施放了该实例**。本场来源只使用实际主题的 `sourceId` 及其可用性。

详情的 `sourceResourcesSha256` 只列该定义图中精确引用资源的内容摘要，`definitionGraphBinding` 包含 Buff typed 根和技能清单的内容摘要。自行编写的规则可据此拒绝未经重新核查的资源变更；这些摘要不是运行时施放来源。`capture.context.v1` 与操作主题的 `data.nativeBinding` 公开实际安装 DLL/metadata 的有效摘要，无效或缺失为 null，不能用预期版本替代实际文件。

## 有界操作观察流

`effects.operations.v1` 默认最多保留并返回 256 项，支持 `filter.limit: 1–256`，不支持按目标或效果过滤。每项包含本地 `index`、原始 `seqId/type/atMs`、编号关系 `sequenceRelation` 和明确类型的 `payload`。已解析的动作内容按当前生成的 protobuf descriptor 保留，包括创建/结束、伤害/治疗、参数修改、计算、存储和读表等运行数据；不是只有 Buff 创建/结束。无已知内容的操作仍保留头部；没有账号、网络端点、Cookie 或原始日志接口。游戏 `seqId` 是上传筛选之前分配的编号，空操作被正常过滤时会跳号，不能据此判断网络丢失。

实时采集通过独立只读旁路为每个 raw op 发一次头，包括无法识别的类型；随后同一操作的规范化效果字段只补全已有项。原追踪、伤害、计时、上传和磁盘日志不接收这些旁路头。`sourceMode: raw-operation-header` 表示该路径；旧日志的 `normalized-only` 会保守报告派生重复或缺号，不能冒充新实战 raw 路径。

raw 头可能先到，效果 `payloadState: pending` 随后在同一 index 以新 `payloadRevision` 补全。页面必须等待它，不能只按 index 去重而漏过补全；必要 payload 为 omitted 或冲突时，派生值应恢复未知。非效果头 `header-only` 仅用于观察序列。单项内容上限 32000 字节，整个投影上限 1500000 字节；容量或消息省略通过返回计数及索引边界明确报告。

新实时源的 `continuity.basis` 为 `source-index-and-capture-integrity`：`sourceIndex` 是本地采集器对每个实际解出的 raw op 分配的严格连续序号，与游戏编号不同；回调失败也会占用位置。每项还带完整 Proto 确定性序列化的 `operationSha256` 和采集质量资料。游戏编号正向跳号记为 `forward_skip`，本身不再撤销计数。游戏编号重复/回退仍保守保持未知，不以部分字段或相似度去重。

`captureIntegrity` 跟踪 `packetsDroppedQueue/decompressionErrors/protobufDecodeErrors/framingErrors` 四个累计指标及 `pendingMultipacks/pendingTransportSegments/currentFrameErrors` 三个状态指标。任何字段缺失、非可信当前帧、未完成组包/缓存段或错误指标变化都会形成未知边界；恢复稳定后须等待之后的新语义锚点。结构损坏的复合帧中，即使先成功解出了某个操作，也不能用该帧里的清层动作恢复计数。TCP 内容冲突不会因为待处理队列清空而被视为恢复。

`continuity.state` 只描述本地接收头和质量段，`continuity.payloadState` 另外描述操作内容是否 complete/pending/omitted；必须同时检查。旧 raw 或 normalized-only 资料缺少新元数据时仍保守采用明确标注的 legacy 规则，不能把过去的跳号直接补成“已完整接收”，也不能在新源缺字段后自动降级。

订阅收到的是滚动窗口。必须检查 `context/streamRevision/firstIndex/lastIndex/lastObservedIndex`、`gapRevision/continuity`、内容版本与省略信息；新窗口跳过上次游标表示操作未完整交付。初始捕获状态始终未知。本地接收资料完整不等于全部游戏本地状态都经网络上报，还需各效果独立的创建、更新、结束和权重证明，不能直接数 UID 或暴击次数当层数。

目标失效通过 `targetContextRevisions[targetId] = {revision, afterIndex, streamRevision}` 精确表达。它涵盖同一全局场景内的目标重置、代数变更、离场、死亡和身份重绑定；服务端快照替换及精确单 UID 结束不会冒充目标重置。页面应取消对应目标的派生状态，且不得用 `afterIndex` 之前的清层动作重新校准。映射最多 2048 项；淘汰通过 `targetContextRevisionOverflow/OverflowAfterIndex/OverflowStreamRevision` 与完整性标记报告，不能因目标键消失就当作从未失效。

`staticConfig` 与 `details.skillParameters` 都是静态配置：包括准确 `stackingType/lifeType`、黑板引用、配置最大层数和模板/SkillPatch 各级参数。SkillPatch 覆盖模板值，显式 AssignPair 才传播到对应 Buff；动态 ID 未解析或缺失资源保留 unresolved。模板、补丁、初始化指令均不能代替当前运行值。

## 游戏事件数据

`game.events.v1` 将既有采集服务已经产生的游戏事件提供给 HTML，不重新采集，不重复解析 protobuf。它是有限的观察窗口，**不是完整当前状态，也不是完整历史日志**。没有出现在窗口里的事件不能解释为没有发生。

```js
const seen = new Set();
let context = null;
const stop = window.endfieldOverlay.subscribe({
  topic: 'game.events.v1',
  filter: {
    eventTypes: ['SC_ATTACH_SERVER_SKILL', 'SC_UPDATE_SERVER_SKILL', 'LOADOUT'],
    limit: 64
  }
}, envelope => {
  if (envelope.state !== 'ready') {
    seen.clear();
    context = null;
    return; // 同时清掉页面保留的旧运行数据
  }
  const data = envelope.data;
  const key = JSON.stringify(data.context);
  if (key !== context) { seen.clear(); context = key; }
  for (const event of data.events) {
    if (seen.has(event.index)) continue;
    seen.add(event.index);
    if (event.payloadState !== 'complete') continue; // 省略不等于空值或零
    // event.type / event.atMs / event.payload 保留明确事件来源和原字段含义
  }
  // 只为当前观察窗口保留去重历史，避免页面自己的集合无限增长。
  for (const index of seen) if (index < data.bufferFirstIndex) seen.delete(index);
});
```

`eventTypes` 使用明确事件名白名单，最多 256 项；省略或空数组表示该主题支持的全部事件类型。可用类型见 ready `data.allowedEventTypes`。`limit` 为 1–256，默认 64，不支持效果或目标筛选；页面可按各消息自己的准确身份字段处理。

每项包含 `{index, type, atMs, payload, payloadState, payloadReason, excludedDiagnosticFieldCount}`。`index` 在当前采集上下文内按所有允许事件共同递增；筛选后出现数字间隔是正常现象，不能据此判断丢包。`atMs` 是采集事件的观察时间，不自动等于官方副本计时或 Buff 生效起点；消息内的时钟值仍保留各自字段含义。回调交付的是当前有限窗口，可重叠、可合并刷新，不能把每次回调都当作一批未读增量。`payloadState: 'omitted'` 时内容不可用，应查看 `payloadReason`，不得继续累计假定完整的状态。

缓冲区最多 256 项且最多 1250000 字节；单条内容最多 1000000 字节，完整投影最多 1500000 字节。单条数据还限制最多 131072 个节点、24 层、每个数组或映射 1024 项、每个字符串 16384 字符。超限时明确省略整条内容，不静默截断成看似完整的对象。检查 `bufferDroppedCount/payloadOmittedCount/projectionOmittedEventCount/projectionReason`、`bufferFirstIndex/bufferLastIndex/lastObservedIndex` 及 `firstIndex/lastIndex`；这些计数只描述接口保留/传递范围，不能代替网络完整性证据。

此主题保留服务器技能的 typed 黑板、等级、潜能、启停、被动和来源信息，以及已接入的队伍/配装/属性变化、计时消息与战斗操作。proto3 的零值和 false 保留；字段默认值不代表额外观测到一次赋值。原始包字节、登录凭据、加密会话密钥和诊断事件不属于此接口。黑板内的游戏参数名按原样保留，不因为恰好叫 token、account 或 hex 就当成凭据删除。没有单位或当前状态关系证明的值继续按原字段提供，不解释成当前层数、百分比或剩余秒数。

## 实际效果：四类证据分开

| 字段 | 意义 | 页面可否当当前层数/剩余时间 |
|---|---|---|
| `raw` | 接口保留的原始观测字段 | 不可直接解释 |
| `wireValues` | wire 中确实出现的值及字段来源；如 duration 单位仍可未知 | 不可推算毫秒或倒计时 |
| `initializationParameters` | 初始赋值指令；`currentValues: false`。间接赋值只给输入 key，不把协议默认 0 当实测值 | 不可当当前值 |
| `runtimeValues` | 已接入并证明语义的当前字段/变量；仍须检查 `fieldAvailability` | 仅可使用标记 known 的字段 |

顶层 `fieldAvailability.<field>` 给出 `state/source/reason`：`known` 表示值有明确证据（真实 0 也可以已知），`unknown` 表示当前缺少观测或关联，`unsupported` 表示尚未接入并证明该语义。缺字段、null、unknown、unsupported 和空观测列表均不能显示为零层。`runtimeValues.variables` 的可用性使用 `fieldAvailability.variables`；单个变量保留 value/source/configIndex 等原证据。

当前通用精确层数 `stackCount` 和精准时间 `durationMs/expiresAtMs/remainingMs` 仍不支持，不能承诺所有 Buff 的层数都能显示。`rate` 是运行倍率参数，**不是层数**；不得除以模板单层数值猜层数，也不得直接当百分比或秒数。未证明单位的变量按原数值呈现。

实际快照的 `coverage: 'observed-only'`、`completeness: 'partial'`、`absence: 'unknown'` 说明这只是已观测集合。`lifecycle` 区分 observed-open、observed-closed 与 context-unknown；closed 是观测到关闭，open 不是全量存在性保证。初始化/运行更新、启停与实例生命周期分开；不能因赋值或 rate 更新凭空创建实例。查看 `omittedOpenCount/omittedClosedCount` 以识别服务端省略记录。

重复 add 的去重边界也受保留历史限制：对应客户端 UID 的闭合历史行仍在内存时，晚到的重复 add 不会使它复活；闭合历史已被淘汰后，旧 UID 的晚到 add 会作为新观测记录，不能承诺跨历史淘汰维持完整实例状态。

## 最小 HTML

文件位于 `src/endfield_pcap/overlay_examples/data-api-minimal.html`。通过客户端的 HTML 宿主添加该本地页面，手工编辑顶部 `const CONFIG` 后重新加载。它没有搜索表单或布局编辑器，也没有武器专属逻辑。

默认以 `target: 'party'` 订阅已有准确队伍映射的实际主题，不假定谁是当前受控角色。示例只展示 `observed-open`，已关闭历史记录不再显示为当前值；保留各字段的 unknown/unsupported 状态。可选 `definitionQuery` 会先分页完成静态检索，再用所得 canonical IDs 订阅实际主题。若同时配置 `effectIds`，使用与查询结果的准确 ID 交集。空查询结果会停止，不会误用空 effectIds 订阅全部效果。定义可离线检索，实战没有对应观测时显示等待；不是零层。

示例只使用宿主给出的内联图片和 DOM `textContent`，不请求外部资源；最多显示 60 条并提示截断。它使用 `runtimeValues/fieldAvailability` 显示已知值及未知/不支持状态，不生成倒计时。实际连接失效时立即清旧，页面关闭时取消订阅。

实际订阅示例只显示当前第一页的开放观测，不自动聚合后续实际页。`hasMore`、消息预算或省略计数出现时，会明确提示显示不完整；客户端的 60 行上限与服务端消息预算是两层独立限制。
