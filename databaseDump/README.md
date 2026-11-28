# databaseDump

这是 GTNH 中文维基工具链中的数据导出、整理与上传子目录。

## 工作原理

整体流程分三段：

1. 先从 Minecraft `dumps/` 里拆出适合 wiki 上传的页面数据。
2. 再扫描本地 `wikitext/` 和 wiki 现有内容，生成上传清单数据库。
3. 最后按清单批量上传页面、图标和图片重定向。

## 主要脚本

- `split_all.py`：读取原始导出数据，合并图标/流体等信息，并切分成 wiki 可上传的小文件。
- `item_names.py generate-config`：生成本地中文图片候选报告，供编辑首选配置时参考。
- `wiki_inventory.py`：扫描本地 `wikitext/`，对比 wiki 现有页面，生成 `wiki_inventory.db`。
- `image_inventory.py`：扫描 `dumps/icons/` 和重定向映射，生成 `image_inventory.db`。
- `guidenh.py`：GuideNH 任务，把内容包仓库里的中文指南转成 `指南:GuideNH/` 页面，见下文“GuideNH”。
- `data_upload.py`：读取 `wiki_inventory.db`，把待上传页面推到 wiki。
- `image_upload.py`：读取 `image_inventory.db`，上传图标文件。
- `image_redirect.py`：读取 `image_inventory.db`，创建 `File:` 图片重定向页。
- `image_audit.py`：只读审计，查计划里每个待传名字和待建重定向在线上的状态，见“文件页与重定向的坑”。

## 数据库

- `wiki_inventory.db`：页面上传任务表，每次运行 `wiki_inventory.py` 都可以重新生成，不提交到仓库。
- `image_inventory.db`：GTNH 图标上传与重定向任务表，会提交到仓库用于保留图片上传状态。
  其他模组的图片状态独立保存在 `source_state/<本地模组名>/<MOD_VERSION>/image_inventory.db`，不提交到仓库。

## 配置

脚本默认从仓库根目录的 `.env` 读取配置。先复制 `.env.example` 为 `.env`，再按本机环境填写：

```bash
copy .env.example .env
```

其中 `DUMPS_DIR` 对应 Minecraft 导出的 `dumps` 目录。当前数据源由 [iouter/GTNH-Dumper](https://github.com/iouter/GTNH-Dumper) 导出，填写时应让 `DUMPS_DIR` 指向 GTNH-Dumper 生成的 dumps 目录。

## 常用命令

在 `.env` 中设置上传目标，例如：

```dotenv
MOD_LOCAL_NAME=GTNH
MOD_REMOTE_NAME=GTNH
MOD_VERSION=2.9.0-beta-1
```

可用 `MOD_LOCAL_NAME` 和 `MOD_REMOTE_NAME` 分别指定本地数据源名和远端目录名。
两项均须填写，缺失或为空时会报错。例如：

```dotenv
MOD_LOCAL_NAME=TwistSpaceTechnology
MOD_REMOTE_NAME=TST
MOD_VERSION=1.0.0
```

此时从 `dumps/filtered/TwistSpaceTechnology/` 读取，上传到 `Data:Versions/TST/1.0.0/`。
`split_all.py` 输出到 `wikitext/Data/Versions/<远端模组名>/<MOD_VERSION>/`；
`wiki_inventory.py` 只扫描和查询该模组/版本，`data_upload.py` 只执行该前缀下的上传和删除任务。
本地模组名为 `GTNH` 时读取 dumps 顶层；其他模组读取
`dumps/filtered/<本地模组名>/`。远端名用于生成上传页面的目录。
模组目录不存在会报错；缺失或空的主输入文件会跳过。NBT 扫描缓存按本地模组名/版本保存在
`databaseDump/source_state/` 中。页面清单支持 filtered 目录中的文件。
修改配置后应重新运行拆分和清单脚本。缺少配置时，按报错提示补齐 `.env` 后重试。
普通 Wiki 页面源码上传仍使用文件对应的页面路径。

数据页（默认所说的“上传”只指这一部分）。`wiki_inventory.py` 对照线上生成清单：本地有的上传，
线上有而本地没有的删除；`data_upload.py` 在全部上传成功后才执行删除（`--no-delete` 只上传）。
上传默认 4 线程（`-t N` 调整）。站点按时间窗口限制编辑频率，短时间写入几百个页面会返回 `ratelimited`
（提示“anti-abuse”）。命中后所有线程一起暂停 `UPLOAD_RATE_LIMIT_WAIT` 秒（环境变量，默认 120），再重试同一个页面，
最多 5 次；仍然被限速才记为失败，留待重新运行。删除同样如此。两次上传不要连续叠着跑：限速是按窗口累计的。

线上标题用 allpages 按前缀列举，比较时下划线与空格视为同一字符。以下情况不删除，记为 `kept_online`：

- 保留目录 `KEEP_ONLINE_DIRS`（`wiki_inventory.py` 中写死）：工具链不生成、但线上需要保留的目录。目前为空。
- 本地整个目录都不存在（例如某个拆分任务因缺输入而跳过）。确实要移除整个目录时用
  `python wiki_inventory.py --delete-dir <目录名>` 点名。

平台保存 JSON 数据页时会把对象键里的 `.` 和 `$` 改成全角的 `．`（U+FF0E）和 `＄`（U+FF04）
（MongoDB 字段名不能含这两个字符；`模块:Database` 查询时也按同样规则转换）。
工具链在 `platform_json.py` 中提前做同样的转换：`data_upload.py` 上传的是转换后的文本，
`page_hash.py` 对 `.json` 文件按转换后的内容计算哈希，所以本地文件、上传内容与线上源码一致，
不会因为这个改写把没改动的页面当成差异。只改键，值和排版原样保留；本地文件仍保持原样（含 `.`）。

### 只处理一部分数据

反复迭代某一份数据（魔导手册、维度、任务书、匠魂材料等）时，拆分和上传都可以只做这一块：

```bash
python split_all.py thaumonomicon dimensions                       # 只拆分这两项
python wiki_inventory.py                                           # 清单（增量，不要加 --refresh）
python data_upload.py --only Thaumonomicon gtnhdimension           # 只上传、删除这两个目录
```

`--only` 后面跟版本目录下的第一级目录名（`Items`、`ItemNames`、`Recipes`、`OreDictionary`、`Ores`、`Aspects`、`Gtmaterial`、
`Ticmaterial`、`Thaumonomicon`、`Questbook`、`Biomes`、`Achievements`、`gtnhdimension`），直到下一个 `-` 开头的参数为止。
上传和删除都只限这些目录；版本配置模块仍要等整个版本的数据页都上传完才发布。
拆分任务与目录的对应：`thaumonomicon` → `Thaumonomicon`，`dimensions` → `gtnhdimension`，`quests` → `Questbook`，`biomes` → `Biomes`，`achievements` → `Achievements`，
`ticmaterial` → `Ticmaterial`，`gtmaterial` → `Gtmaterial`、`Ores`；`items` 还会刷新 `ItemNames`、`Aspects`。
Dumper 端可用自动导出的 `tasks` 只导出对应数据（见 GTNH-Dumper README“只导出部分数据”）。


```bash
python split_all.py
python wiki_inventory.py
python data_upload.py
```

图片（只在明确要求上传图片时运行；“全量上传”也要先确认是否包含图片）：

```bash
python image_inventory.py
python image_audit.py --output audit.json   # 只读：先看有多少名字会撞上文件页的坑
python image_upload.py --delete-existing
python image_redirect.py --delete-existing
python image_audit.py                       # 做完再审计一遍，两项“要先删”都应为 0
```

图片脚本默认 4 线程，遇到站点反滥用限速时全部线程暂停（`UPLOAD_RATE_LIMIT_WAIT` 秒，默认 120）后重试；线程别开太多。

## 不导入维基的组合变体

Dumper 忠实导出游戏里的内容，哪些不进维基只在工具链决定（`item_exclusions.py`），对物品页、物品图片和配方页生效。
`split_all.py` 会打印排除的变体数、各规则去掉的配方数和改写的流体引用数。

| 内容 | 处理 |
|---|---|
| Forge Microblocks、Extra Utilities 显微方块（全部形状），BC 与 AE2 伪装板 | 去掉；仍被保留配方或任务书用到的具体变体保留。只产出被去掉变体的配方一起去掉 |
| 带图案的旗帜 | 同上；空白旗帜保留，旗帜图案配方随之去掉 |
| 匠魂弩箭 | 每个配方文件、每种箭头只留箭杆材料编号最小的一条配方；物品只留被保留配方或任务书用到的，某种箭头一个都没有时留一个 |
| Ztones 换样式配方（ofanix ＋ 同一方块） | 去掉这类配方，方块保留 |
| 装了流体的容量瓶（GT 与 GT++ 共 4 种）和水晶容器 | 去掉；配方或任务书用到的具体物品保留。这一项从不删配方。空容器、通用流体单元、GT 流体展示物品保留 |
| NEI 自带的流体展示物品 `NotEnoughItems:neiFluidDisplay` | 全部去掉；只放流体的格子从 `inputItems` / `outputItems` 移到 `inputFluids` / `outputFluids`，key 为 `fluid.<名称>`，数量是毫桶数（配方界面按固体格的顺序摆位，流体留在固体格里会挤掉铸模） |

任务书只按 `betterquesting/itemKeys.json` 里的图标、要求物品和奖励保护；`taskMatches` 不算，
忽略 NBT 的任务会接受一种物品的全部变体。成就、魔导手册和维基手写页面的引用不在保护范围内。

某个配方文件被整个去掉时，`split_all.py` 会清掉本地对应的页面；已经上传到维基的页面和图片不会自动删除。

## 矿辞与物品依赖

`split_all.py oredict` 使用当前模组、版本已生成的 `Items` 分页（JSON 或 TABX），
将 `:32767` 通配成员展开为实际导出的 damage/NBT 物品 key。首次处理该版本时运行：

```bash
python split_all.py items oredict
```

Items 目录缺失、没有分页或分页中没有物品记录时，矿辞处理会报错，保留已有矿辞页和
`oredictionary-associations.json` 报告。Items 生成成功后才可单独运行 `python split_all.py oredict`；
物品数据变化后应先重新生成 Items。默认的 `python split_all.py` 已按 Items、矿辞的顺序执行。

## 魔导手册（Thaumonomicon）

`python split_all.py thaumonomicon` 读取 GTNH-Dumper 的 `tc4thaumonomicon.json`，写到
`Data:Versions/<远端模组名>/<版本>/Thaumonomicon/` 下三个子目录，每个都是数字分页
（`0.json`、`1.json`…），页内是一个顶层数组，线上 Lua 用 `$unwind` 查询：

| 子目录 | 页内数组 | 内容 |
|---|---|---|
| `Categories/` | `tc4ThaumonomiconCategories` | 分类：名称、图标、背景、网格范围 |
| `Aspects/` | `tc4Aspects` | 要素：颜色、合成组件、图标（仅 `MOD_LOCAL_NAME=GTNH`） |
| `Researches/` | `tc4ThaumonomiconResearches` | 研究：坐标、依赖、要素消耗、图标、全部页面 |

记录保持导出器的原字段，按分类、列、行排序。每页严格不超过 2,000,000 字节。
物品与研究的关联由线上 Lua 根据配方数据完成，本工具不做。
默认任务会包含它，也可单独运行；输入文件缺失或为空时跳过。
研究的 `Category` 是本地化分类名，`CategoryKey` 是分类 key，对应分类记录的 `Key`。

它取代了旧的 `Tc4research` 研究表：Dumper 的 `tc4thaumonomicon.json` 包含 `tc4research.json` 的全部字段，
工具链不再读取 `tc4research.json`，也不再生成 `Tc4research/` 数据页。

### 范围划分

每次运行处理 `.env` 中选择的一个模组与版本。`MOD_LOCAL_NAME=GTNH` 时读取完整导出，
按研究的 `sourceMod` 排除 [config/addon-mods.json](config/addon-mods.json) 列出的额外附属；
Thaumcraft、dreamcraft、KekzTech 等基础包模组和缺少来源字段的旧记录保留。
公共列表填写注册 Mod ID，目前包含 `TwistSpaceTechnology`、`sciencenotleisure` 和
`programmablehatches`，目前只用于魔导手册的范围划分。

处理附属时，`MOD_LOCAL_NAME` 填注册 Mod ID，`MOD_REMOTE_NAME`、`MOD_VERSION` 指定目标目录。
附属优先读取自身 filtered 目录里的 `tc4thaumonomicon.json`；缺少独立导出时，从完整导出按
`sourceMod` 精确选择注册者，比较时忽略大小写。该字段由 Dumper 在研究注册时记录，研究可以位于任意分类。
分类只保留仍有研究的，附属不会带上基础包的分类；要素没有归属，只写入 GTNH。
尚无该附属的 filtered 目录时，使用 `python split_all.py thaumonomicon` 单独处理；
默认全部任务及其他任务需要附属自己的数据目录。
完整导出尚无来源字段时跳过附属提取，已有独立 filtered 导出继续使用。
新增额外附属时，将注册 ID 加入公共列表，再分别处理 GTNH 和该附属的目标版本。

**贴图**不由本工具处理，已由维基一次性上传，Dumper 导出的 `tc4thaumonomicon-assets/` 不进工具链。
数据里的贴图用 `域名:路径` 标识（如 `thaumcraft:textures/gui/gui_researchback.png`，
文字里的 `<IMG>` 标记也是这个格式），对应维基文件：

`File:Thaumonomicon-Asset-<域名>-<去掉 textures/ 的路径，斜杠换成短横>.<扩展名>`

扩展名规则：分类的 `Background` 字段指向的背景图是 `.webp`（有损），其余贴图保持原样的 `.png`。
例如 `thaumcraft:textures/items/alumentum.png` 对应 `Thaumonomicon-Asset-thaumcraft-items-alumentum.png`。
研究图标若是物品（`Icon` 字段），用物品图标，不在此列。

## 维度（Dimensions）

`python split_all.py dimensions` 把 GTNH-Dumper 的 `gtnhdimension.json` 写到 `gtnhdimension/0.json`，默认任务包含它，
只有 `MOD_LOCAL_NAME=GTNH` 处理，输入缺失时跳过。

新版 Dumper 输出 `{"dimensions": [...]}`：每条记录以 `key` 标识，GTNEI 字段（`abbreviatedName`、`internalName`、`fullName`、
`originalName`、`localizedName`、`tier`、`stoneTypes`）放在 `gtneiInfo` 里，还追加了 GTNEI 没收录的 Forge 维度（`key` 为 `dim:<ID>`）。
线上 `模块:Database`、`模块:条目/维度`、`模块:条目/属性接口` 读取的是平铺结构，所以发布时把 `gtneiInfo` 的字段提回顶层，
数组名仍为 `abbreviatedName`；其余字段（维度 ID、Provider 参数、`galacticSpaceInfo` 等）和 `key` 原样保留。
旧版 Dumper 的 `{"abbreviatedName": [...]}` 原样通过。字段含义见 GTNH-Dumper README 的“维度与星系参数”。

## 数据黑名单（导出扰动）

同一整合包、同一导出器两次导出，仍有少量内容 GTNH-Dumper 固定不了（神秘时代按配方推算的要素、Mob Info 读到的
暮色森林生成群系等）。`config/data_unstable.json` 列出这些记录和字段，`split_all.py` 切分时处理（只对
`MOD_LOCAL_NAME=GTNH`），日志里以“数据黑名单:”开头：

- `keep` / `keepFile`：沿用记下的值。值记在 `source_state/GTNH/<版本>/stable/`（不提交仓库），第一次切分某个版本时记录。
- `keepOrder`：沿用记下的顺序，内容照常更新。
- `sort`：列表按固定顺序排列。

换了新版本会重新记录，不会带上旧版本的值。同一版本内这些字段即使真有变化也不会更新：删掉对应的状态文件即可接受新值。
别的机器要和线上保持一致，先用线上页面对应的那份导出记录一次：

```bash
python data_stability.py seed <dumps 目录> <版本>
```

名单只依据实际比对结果添加：用同一份代码导出两次，比较两份 `dumps`，把确认固定不了的记录写进规则。

## 生物群系（Biomes）

`python split_all.py biomes` 读 GTNH-Dumper 的 `biomes.json`，默认任务包含它，只有 `MOD_LOCAL_NAME=GTNH` 处理：

| 目录 | 内容 |
|---|---|
| `Biomes/List/N.json` | 数组 `biomes`，每个已注册群系一条：ID、英文名、类、温度、降水、高湿度、降雪、降雨、打雷、根高度、高度变化、颜色、地表与填充方块、BiomeDictionary 标签、林业气候、IC2 作物加成、各类生物的刷怪表（实体、权重、群组大小）、生态之书扩展版页码 |
| `Biomes/Books/0.json` | 巫术的两本书：`bookOfBiomes`（季节变迁仪式指南，每类群系一页）与 `bookOfBiomesExtended`（每个群系一页，含游戏内显示的降雨、温度、降雪、打雷四行，中英文各一份，以及分节） |
| `Biomes/Tags/0.json` | `types`（BiomeDictionary 标签）、`forestryTemperature`、`forestryHumidity` 三张表，每项 `{id, zh, count}`：游戏中的名字、中文名（配置中没有则为 null）与群系数 |

| `Biomes/Dimensions/0.json` | `method`（Dumper 的采样方法：种子、范围）与数组 `dimensions`，每个维度一条：`dimensionId`、可生成的群系 ID `biomes`、管理器来源 `chunkManagerSource`（`world-type` 的维度群系不可靠，线上不展示）、取不到时的原因 `errors`。读 `dimensionbiomes.json`（Dumper 的 `dimensionbiomes` 任务），文件不存在时跳过；线上按 `dimensionId` 与 `gtnhdimension` 关联 |

游戏里没有群系名和标签的中文，拆分时从 `config/biome_names.json` 补 `nameZh`、`typesZh`；林业气候的中文
`forestryTemperatureZh`、`forestryHumidityZh` 用游戏里的译名（`biomes.json` 的 `forestryClimates`）。群系按 ID → 英文名加书页码 → 唯一的英文名匹配；缺中文名的会列出来，补进配置即可。
这份配置最初取自人工整理的 `Data:生物群系.json`（已由本目录的数据取代）。

RWG1（类名 `rwg.*`，ID 200–237）与 RWG2（`rwg2.*`，ID 300–329）有一批英文名相同的群系。整合包默认的世界类型用 RWG2，
它的中文名不加括注，RWG1 的加“（RWG1）”。两代都要按 ID 配置：少了 RWG1 的条目，它会按英文名匹配拿到 RWG2 的名字。

## 成就与奖杯（Achievements）

`python split_all.py achievements` 读 GTNH-Dumper 的 `achievements.json`，默认任务包含它，只有 `MOD_LOCAL_NAME=GTNH` 处理：

| 目录 | 内容 |
|---|---|
| `Achievements/List/N.json` | 数组 `achievements`，每个已注册成就一条：`id`（统计 ID，如 `achievement.portal`）、中英文名称与描述、所在成就页 `page`、坐标、前置 `parent`、`special`、图标 `icon`；Amazing Trophies 定义的成就另有 `source` 与条件 `condition` |
| `Achievements/Pages/0.json` | `pages`：成就界面翻页的顺序，原版页 `Minecraft` 在前 |
| `Achievements/Trophies/0.json` | 数组 `trophies`：Amazing Trophies 的奖杯，含中英文名、获得条件 `condition`、模型 `model`，以及授予它的成就 `achievement` |

成就与奖杯带 `items`（关联物品，写法同物品表的 `key`；带 NBT 的图标与奖杯本身是短 key）：成就取图标与条件中的物品（`inventory.add`、`item.use.finish`）；
奖杯取奖杯方块本身、物品模型展示的物品、奖杯自身条件中的物品（少数奖杯不经成就直接颁发，如冰淇淋冠军奖杯），加上授予它的成就的物品（方块拼成的结构模型不算）。物品表据此写 `achievements`、`trophies` 字段。
原版与其他模组的成就由代码触发，只有图标可作关联，所以这类关联只表示“图标是这个物品”。
条件只写 `id` 与 `meta`、而物品表中该 rawKey 只有一个带 NBT 的变体时（如历代星门成就要求的任务用纸 `minecraft:paper:1–5`），
成就与奖杯另带 `conditionItem`（该变体的 key），线上显示条件物品时优先使用。

## 实体（Entities）

`python split_all.py entities` 读 GTNH-Dumper 的 `entities.json`，写 `Entities/List/N.json`（数组 `entities`），默认任务包含它，
只有 `MOD_LOCAL_NAME=GTNH` 处理。每条记录以 EntityList 注册名 `id` 为键，字段见 GTNH-Dumper README 的“实体”一节
（名称、来源模组、类别、基础属性、MobsInfo 的精英怪/灵魂瓶/生成来源/掉落、Ender IO 电动刷怪笼）。
群系刷怪表只存实体 `id`，线上（`模块:实体`）按 id 查名称，不在其他表里重复写名称。

## 蜜蜂（Bees）

`python split_all.py bees` 读 GTNH-Dumper `bees` 任务的 `bees/*.json`，写 `Bees/<表>/N.json`，默认任务包含它，只有 `MOD_LOCAL_NAME=GTNH` 处理。
游戏里读得到的内容都放进来，同一内容只存一处：**物品只记 key**（名称、图标查物品表），**气候只记 id**
（与群系记录同一套，中文名查 `Biomes/Tags`）。游戏里没有、靠人读代码写的说明（比如特殊效果的实际作用）留在维基页面上。

| 目录 | 页内数组 | 内容 |
|---|---|---|
| `Species` | `beeSpecies` | `uid`、中英文名、拉丁学名、作者、游戏内描述、分类 `branch`、气候 id、颜色、显隐性与各项标记、研究复杂度、欣喜条件、产物与特产（物品 key、数量、基础概率）、基因组模板（染色体 → 等位基因 uid）、这个蜂种有哪些蜂类物品，查物品表的 `beeSpecies` |
| `Mutations` | `beeMutations` | 亲本、子代、基础概率（百分数，未乘养蜂模式与蜂箱修正）、附加条件原文 |
| `Alleles` | `beeAlleles` | 等位基因中英文名、所属染色体、数值（工作速度、寿命、范围等） |
| `Branches` | `beeBranches` | 分类树节点 |
| `Frames` | `beeFrames` | 框架对杂交、寿命、产量等的倍率、耐久与提示文字 |
| `Flowers` | `beeFlowers` | 每类采蜜对象认的方块（可种植的权重大于 0）；靠代码判断的规则只能列出类名 |
| `Hives` | `beeHives` | 野生蜂巢掉落（物种、权重、附带物品）；Forestry 注册的蜂巢另有生成概率与适宜群系 id |
| `Meta` | `beeMeta` | 染色体顺序、世界的养蜂模式与全部模式的倍率、研究催化物 |

引用到但物品表里没有的 key，以及指向不存在的物种/等位基因的引用，切分时会打印 WARNING。

### 蜂类物品的身份

所有蜂后是同一个物品（公主蜂、雄蜂、幼虫也是），蜂种只在 NBT 的基因组里。Dumper 给物品表和配方里的蜂类物品写
`beeSpecies`（蜂种 uid，与上面各表同一套）和 `beeType`（`QUEEN` / `PRINCESS` / `DRONE` / `LARVAE`），工具链原样保留。
只按 `beeSpecies` 查得到一个蜂种的全部蜂类物品。配方里的蜂找不到完全一致的物品时，按这两个字段找展示物品；找不到或不唯一时
`displayKey` 为 `unresolved-bee:<类型>`，条目仍带这两个字段。缺身份或与基础物品对不上的列在诊断里。

## 任务书（Questbook）

`python split_all.py quests` 把任务书拆到 `Questbook/` 下的两个目录，默认任务包含它：

```text
wikitext/Data/Versions/<远端模组名>/<版本>/
  Questbook/QuestLines/0.json, 1.json, ...
  Questbook/Quests/0.json, 1.json, ...
```

输入来自 GTNH-Dumper 复制到 `dumps/betterquesting/` 的源文件（与其他数据同一次导出）：
`DefaultQuests/`（任务、任务线、`QuestLinesOrder.txt`）和 `lang/zh_CN.lang`。只有 `MOD_LOCAL_NAME=GTNH` 处理，
输入缺失时跳过。实现在 `quests.py`，随 `wiki_inventory.py` 与 `data_upload.py` 一起上传；
原来单独的 `questUpload/` 工具已并入。每页最多 1,800,000 字节，两空格缩进。

### 数据结构

任务线页面根字段为 `questLines` 数组，任务页面为 `quests` 数组。记录保留源字段，按以下规则整理：

- NBT 字段的 `:数字` 类型后缀移除，`:9` 列表按数字索引排序并转换为 JSON 数组。
- 所有 `…IDHigh:4` / `…IDLow:4` 对合并为 `…ID` 字符串：两个有符号 long
  以大端排列，URL-safe Base64 编码并去掉 `=`。任务 ID、任务线 ID、前置引用和布局引用使用相同格式，
  不会经过浮点数；其他 NBT long 也保存为十进制字符串，避免 Wiki/JavaScript 丢失精度。
- `properties.betterquesting.originalName` / `originalDesc` 保留源文件中的英文，
  `name` / `desc` 分别通过
  `betterquesting.quest.<ID>.name/desc` 或 `betterquesting.questline.<ID>.name/desc` 汉化。
  汉化字段中的 `%n` 转为真正换行；保留 `§` 格式码及 `[url]`、`[note]` 等任务书标签供展示端处理。
- 任务保留 `preRequisites`、`tasks`、`rewards` 和全部属性，物品及其 NBT 不裁剪。
- 有 Dumper 的 `itemKeys.json` 时，带 NBT 的物品堆栈（改名的奖励、任务用纸等）加 `key` 字段，即物品表中该变体的 key；
  线上显示用它，按物品反查任务仍用 `id` 加 `:Damage`。
- 任务线增加 `quests` 数组，包含 `questID`、`x`、`y`、`sizeX`、`sizeY` 等原布局字段；
  增加从 0 开始的 `order`，按 `QuestLinesOrder.txt` 顺序输出。
- 同一个任务可出现在多条任务线中，任务正文只输出一次。

缺少汉化时保留原文并打印缺失键。
源任务书中指向缺失任务的引用会保留并警告。重复 ID、无效 JSON、任务线顺序不一致等错误会阻止写入。

## GuideNH

`python split_all.py guidenh` 把 [GTNH-Guide-Pack](https://github.com/GTNewHorizons/GTNH-Guide-Pack)（GuideNH 的内容包；GuideNH 是面向 Minecraft 1.7.10 的游戏内指南框架）里的**中文**指南转成维基页面，默认全流程会执行。
只有 `MOD_LOCAL_NAME=GTNH` 处理；没有配置内容包仓库时跳过。内容包采用 CC BY-NC-SA 4.0，每页页首都带来源、版本和许可说明。

### 是否解析

默认解析。在 `.env` 设 `GUIDENH_PARSE=0`，或给脚本加 `--no-guidenh`（只影响这一次）可以整个关掉：

| 脚本 | 关掉后 |
|---|---|
| `split_all.py` | 不读内容包：跳过 `guidenh` 任务，`items` 也不写物品的 `guidenhPages` |
| `wiki_inventory.py`、`data_upload.py` | 不扫描、不上传、不删除指南页面和指南导航框模块 |

注意 `items` 在关掉解析（或开着解析但读不到内容包）时生成的物品数据不带 `guidenhPages`，上传后会抹掉线上已有的指南关联。
只是不想生成或上传指南页面时：切分保持解析开启，任务列表里不写 `guidenh`；清单和上传加 `--no-guidenh`。

### 配置（`.env`）

| 配置项 | 含义 |
|---|---|
| `GUIDENH_REPO` | 内容包仓库链接。首次运行克隆到 `databaseDump/source_state/GuideNH/repo`，之后每次运行 `fetch` 并快进更新。仓库含约 200 MB 的 Git LFS 文件，需要安装 git-lfs |
| `GUIDENH_REF` | 可选，分支或标签；不填跟随默认分支 |
| `GUIDENH_DIR` | 可选，改用已有的本地克隆（只读取，不克隆也不拉取；设置后忽略 `GUIDENH_REPO`） |
| `GUIDENH_NAMESPACE`、`GUIDENH_PAGE_PREFIX` | 可选，页面所在的命名空间和前缀，默认 `指南` 与 `GuideNH` |
| `GUIDENH_SITE_URL` | 可选，官方网页版的站点根地址，默认 `https://www.gtnewhorizons.com/GTNH-Guide-Pack`；页面里的链接是 `<根>/guides/<模组>/guidenh/zh_cn/<页面>.html` |

转换需要 PyYAML（`python -m pip install PyYAML`）；缺少时任务会提示并跳过，不会中断默认流程。

### 做什么

1. 同步内容包仓库，读取 `assets/<模组>/guidenh/_zh_cn/**/*.md`（`templates/` 下的页面模板不单独成页）。
2. 转换成 `wikitext/指南/GuideNH/<模组>/<页面路径>.wikitext`，对应维基页面 `指南:GuideNH/<模组>/<页面路径>`，另生成总览页 `指南:GuideNH/总览`。
   页面里的物品 ID（`modid:name:meta`）对照本版本已拆分的 `Items`（还没有时用 `DUMPS_DIR` 的 `advitempanel.json`）解析成物品 key；
   带 NBT 的写法按 SNBT 内容（与字段顺序无关）匹配 `Items` 里的哈希后缀 key，匹配不上则用该物品不带 NBT 的版本。
   页面标题是路径，所以用 `{{DISPLAYTITLE:指南:GuideNH/正文标题}}` 显示正文开头的一级标题（没有一级标题的页面回退到侧栏的 `navigation.title`），并去掉正文里重复的那个标题。导航框、上级路径和子页面列表的页面名也用同一个标题，侧栏标题（可能很长）只作回退。
3. 3D 场景（`<GameScene>`）使用仓库里 `website-export.zip`（官方静态站的导出）预渲染的静态预览图，按场景在页面中出现的顺序对应；不做交互式 3D。
   每张预览图的说明里和页首说明框里都带本页的官方网页版链接（可以旋转查看场景）。
4. 页面用到的图片放进 `source_state/GuideNH/images/`，写出上传计划 `image-plan.json`。
5. 写出转换报告 `source_state/GuideNH/guidenh-report.json`：未能解析的物品、坏链接、未识别的处理器、不支持的标签、场景数量对不上的页面，
   以及“md 在静态站导出之后改过、场景预览可能偏旧”的页面。

不要手工在 `指南:GuideNH/` 前缀下建页面：`wiki_inventory.py` 会把线上该前缀下本地没有的页面列为待删除（和数据页一样，本地整个目录缺失时只记为 `kept_online`，要删除用 `--delete-dir GuideNH` 点名）。
修正内容请到内容包仓库提交，维基上的页面是生成的副本。

### 页面模板与导航框

生成的页面只调用维基模板、传参数，文字和版式放在模板里，以后微调直接改模板，不需要重新生成页面。模板在编辑仓库的 `wikitext/模板/` 下，随常规编辑流程维护：

| 模板 | 用途 |
|---|---|
| `{{GuideNH页首}}` | 页首：上级页面导航、来源与许可说明、官方网页版链接、作者与日期，并加入分类“GuideNH” |
| `{{GuideNH关联物品}}` | 页首声明框后面的“关联物品”：`blockquote` 加 `{{Columns}}` 的多列无序列表，不单独成章节 |
| `{{GuideNH场景}}` | 3D 场景的静态预览图（基于 `{{Thumb}}`），说明里指向官方网页版 |
| `{{GuideNH异步导航}}` | 页面底部的导航框：用站点通用的 `async-load-container` 在浏览器里异步加载 `{{GuideNH导航}}`（与条目模板的导航相同）。导航框带数百个物品图标，直接嵌入时每次解析要多花二十多秒，所以页面不直接写 `{{GuideNH导航}}` |
| `{{GuideNH导航}}` | 导航框本体，数据来自 `模块:Navbox/GuideNH`；只由异步加载器调用 |

导航框数据 `模块:Navbox/GuideNH`（写法见 `Project:沙盒/navbox`）是整个 GuideNH 共用的一个模块，由任务按内容包的导航树生成，
写到 `wikitext/Module/Navbox/GuideNH.lua`，和页面一样纳入清单：本地不再生成的旧模块（含早期按内容包拆分的 `GuideNH/<模组>`），线上会被列为待删除。
每个内容包是一个顶层分组，其下按导航树分组；兄弟页面的顺序与游戏里一致（`navigation.position` 大的在前，同值按侧栏标题 `navigation.title`），
子页面列表、总览页的列表同样如此。页面的 `navigation.icon`（物品 ID）能解析到有静态图标的物品时，导航框项目带这个图标
（取自 Items 的 `iconName`；动画图标和解析不到的不带）；有子页面的页面成为分组，分组第一项“总览”是页面自己，带它的图标。
页首的“关联物品”取自页面表头的 `item_ids`（找不到的物品记入报告并跳过）。

### 物品与指南页面的关联（guidenhPages）

`split_all.py items`（只在 `MOD_LOCAL_NAME=GTNH` 时）会给物品记录加 `guidenhPages`：页面表头 `item_ids`（以及 `item_id`）声明的关联页面，
**只记页面后缀**（如 `gregtech/multiblocks/hv/cleanroom`），前缀 `指南:GuideNH/` 由维基端的 Lua 拼接；没有关联则没有该字段。
写法同物品 key：meta 为 0 时省略，带 NBT 的写法忽略 NBT（关联到该注册物品的全部变体），meta 写 `*` 或 `32767` 表示全部 meta；
页面按导航 `position` 从大到小排列。条目模块（`模块:条目/数据源`、`属性接口`、`类型`）据此在信息框的“GuideNH”行（位于“英文维基”与“源代码”之间）显示链接，
链接文字取该页开头 `{{DISPLAYTITLE:…}}` 声明的标题。这个字段会改动大部分 `Items` 分桶页，上传 Items 是正常的数据页上传，但数据量大。

### 页面上传

指南页面和数据页走同一条流程，不需要额外步骤：

```bash
python split_all.py                      # 默认全流程，包含 guidenh
python wiki_inventory.py
python data_upload.py                    # 数据页与指南页面；机器人账号，编辑摘要为“机器人：同步 GuideNH”
python data_upload.py --only GuideNH     # 只处理指南页面
```

`--only` 后写 `GuideNH`（大小写不限）只选指南页面，写版本目录名（如 `Quests`）则不含指南页面。内容哈希不变的页面不会重传。

### 图片上传（只在明确要求上传图片时运行）

指南图片是第三种图片种类，沿用图片上传的全部规矩（`image_check.py`）：本地 sha1 与线上相同不传，状态记在 `source_state/GuideNH/image_inventory.db`，重跑不会重传。

```bash
python image_inventory.py --kind guidenh
python image_upload.py --kind guidenh
```

文件名：页面图片为 `GuideNH_<模组>_<路径>.png`，场景预览为 `GuideNH_scene_<内容哈希前 16 位>.png`；场景预览由内容决定文件名，
内容变了是新文件，不会覆盖线上旧图。没有中文别名重定向，`image_redirect.py --kind guidenh` 无事可做。

### 转换规则

| GuideNH 写法 | 维基 |
|---|---|
| Markdown 标题、列表、粗斜体、删除线、表格、围栏代码、引用 | 对应的维基文本；`> [!NOTE]` 等提示块用 `{{Mbox}}`；围栏代码用 `<pre>` |
| `<ItemLink>`、`<ItemImage>`、`<BlockImage>` | `<span class="db-item">@key</span>`、`db-icon`（`{{DatabaseWrapper}}` 统一换成 `{{Item}}`、`{{Icon}}`）；紧跟的同一物品图标合并；方块 3D 小图改用物品图标 |
| `<RecipeFor>`、`<RecipesFor>`、`<Recipe>`、`<RecipesUsage>` | `<span class="db-recipe" data-output="@key">`（即 `{{Recipe}}`）；配方类型按处理器名对应到维基类型，认不出的不填 |
| 连续的单独图标（整段只有 `<ItemImage>`、`<BlockImage>`；一个 `<Row>` 里的多个图标也合并） | 一个 `{{IconList}}` 的连续参数（整页包装时写 `<span class="db-icon-list">`），不是一个图标一个框 |
| `<Color>` | 带颜色的 `<span>`；游戏里为深色背景挑的亮色换成白底能读的同色系 |
| `<GameScene>`（及其子标签） | `{{GuideNH场景}}`（静态预览图），位置按 `align` |
| `<FloatingImage>`、图片 | `{{Thumb}}`、`[[File:…]]`，宽度不超过原图 |
| `<Row>`、`<Column>` | `display:flex` 的 `<div>`、带浮动的 `<div>` |
| `<ContentTabs>` / `<Tab>` | `<tabber>`（内部用普通 `{{Item}}`、`{{Recipe}}` 模板） |
| `<SubPages>`、`<Category>` | 转换时按导航树、`categories` 表头生成的链接列表 |
| 页面链接 `x.md#锚点`、`modid:path.md` | `[[指南:GuideNH/模组/页面#标题文字|文字]]`，找不到目标时只保留文字并记入报告 |
| `<Latex>`、`$$公式$$` | MathJax：行内写 `$…$`，独占一段写 `$$…$$`，公式外包 `<nowiki>` 防止维基语法和模板参数分隔吞掉；有公式的页面顶部加 `{{#html:MathJax3}}`（和维基其他页面一致）。`<math>` 在这个维基上不可用 |
| 函数图、思维导图等交互内容 | `{{Mbox}}` 提示，请在游戏中查看 |

整页正文包在 `{{DatabaseWrapper|html=…}}` 里：物品与图标共用一次数据库查询，配方为配方数加 4 次，
避免大页面超过每页 100 次高开销函数的限制（未包装时 `nac` 页要 192 次并出现 Lua 错误，包装后 2 次）。
包在模板参数里的正文不能出现裸竖线，所以表格输出为 HTML 表格；`<tabber>` 的内容不经过包装器，里面改用普通模板。

### 已知限制

- 没有 3D 交互，也不渲染结构投影；场景预览取自静态站导出，之后改过的场景需要更新内容包里的 `website-export.zip`。
- `<RecipesFor>` 等没有给出处理器时不填配方类型，查询是否命中取决于数据库；页面里没有数据库记录的物品（新版本加入的、NBT 变体对不上的）按 `<code>ID</code>` 保留并记入报告。
- 任务书绑定（`bindings`）、`item_ids` 等表头只用于导航，没有写入页面。

## 配方输入与输出归类

`split_all.py recipes` 在关联物品 NBT 和切分页面前，使用 `recipe_roles.py` 按配方来源归类
NEI 的附加槽位。已确认的燃料、灌注台试剂和作物基因提取器的数据介质写入 `inputItems`，
副产物和掉落写入 `outputItems`；巫师烤炉分别归类燃料、黏土罐与副产物。
归类保留候选顺序、NBT、概率和不消耗标记，缺少燃料的烤炉保留对应输入空位。

研究站中已写入结果的数据棒、魔力池催化条件及其他展示集合继续保存在 `otherItems`，
由对应界面解释和显示。已归类的数据可直接用于输入、输出查询。

## 图片上传

`image_inventory.py`、`image_upload.py`、`image_redirect.py` 同样读取 `.env` 的模组配置：

- 本地模组名为 GTNH 时使用 `dumps/icons/`，其他模组使用 `dumps/filtered/<本地模组名>/icons/`。
- 中文图片重定向使用预处理生成的 `source_state/<本地模组名>/<版本>/image_redirect.resolved.csv`。
  原始 `image_redirect.csv` 保留作参考。重名选择规则见下文。

依次运行图片清单、图片上传、图片重定向三个脚本。非 GTNH 模组首次运行会创建独立数据库，
GTNH 保留已有数据库和上传记录。图片任务只选择当前来源中存在的文件，重定向任务只选择当前 CSV
计划中仍有效且目标图片已上传的记录。失败的图片保持待更新状态，重复运行可以重试。

三个图片脚本都会在执行前通过 `image_plan.py` 生成去重计划，需要安装 Pillow：
`python -m pip install Pillow`。数据源带有 GTNH-Dumper 的 `icon-hashes.json` 时，直接用其中的像素哈希按当前导出重建分组，
不解码图片；没有清单时沿用已保存的计划。文件 sha1 按（文件名、大小、修改时间）缓存在
`source_state/file-sha1-cache.json`，重跑时未变化的文件不再读取。运行 `image_inventory.py` 生成本地计划和状态，随后由上传脚本执行。
计划保存在同一状态目录的 `image-upload-plan.json`，包含实体上传列表、最终重定向和各组哈希。

只在同一注册物品 `modid:name` 内比较不同 damage/NBT 变体。哈希覆盖尺寸与完整 RGBA
像素，压缩方式和文字元数据不同不影响比较；动画条带比较全部帧，动画 WebP 与 APNG 比较逐帧合成后的完整 RGBA 画布、帧时长、循环次数和默认图像。
同图优先选无 NBT、damage 为 0 的图片；缺少该项时依次按无 NBT、零 damage、damage 数值、
完整 shortKey 和文件名排序。每个注册物品分别选择代表图片。

同图变体保留自己的文件名，通过重定向指向代表图片并退出上传列表；中文名先按人工配置
确定物品，再直接指向该物品最终代表图片。因此中文名和变体别名都是一级重定向。
物品 key、NBT、配方数据、本地图片和已上传文件历史均保留。额外素材沿用原有上传方式。

Wiki 图片标题为 `File:<图标文件名>`；模组和版本用于选择数据源及本地清单。

图标格式跟随导出：GTNH-Dumper 默认导出 WebP（动画是单个动画 WebP），`-LegacyPng` 或旧导出是 PNG（动画是横向帧条）。
两种都能走同一套流程，中文别名的扩展名跟随所指向的图标。物品数据另带 `iconPixelated`、`animationPixelated`
（导出记录了才有），供前端决定是否按像素风缩放。

WebP 物品数据里的 `iconName` 是实际上传的文件：被同图合并掉的变体写合并目标的名字，因为前端按文件名直接算原图地址，
不跟随文件重定向（2.9.2 中约 29% 的物品记录属于这种）。静态图和动画分别合并，动画名不等于 `<iconName>_dynamic.webp`
时另记 `animationName`。PNG 数据不变，`[[File:]]` 会自己跟随重定向。GuideNH 导航框数据表按 PNG 拼文件名，WebP 图标在前端支持前不写入。

### 实体图片（`--kind entities`）

数据源是 GTNH-Dumper `entityimages` 任务的 `dumps/entity-images`（连同 `entity-images.json`、`entities.json`），
扩展名跟随导出。计划里的重定向只有实体中文名指向头像（同名实体原版优先，与物品图片重名的不建）。

重定向名与目标的扩展名一致：换图片格式时不建旧扩展名指向新格式的重定向，线上旧格式的实体图片和它们的重定向原样保留。

换一种图片格式时用单独的版本目录记状态（如 `MOD_VERSION=2.9.2-webp`），物品重名判断读的也是该版本的
`image_redirect.resolved.csv`。

### 文件页与重定向的坑

曾经上传过文件的 `File:` 名字，之后再编辑成 `#REDIRECT [[File:目标]]`，重定向不会生效：文件还在，页面文本只被当作图片描述。
反过来，现在是重定向页的名字，也不能直接上传图片。两种情况都必须先删除这个文件页（连同文件），再新建重定向或重新上传。
删除可以在站点上恢复，不是危险操作。

- 判断一个名字下有没有“自己的文件”，不能只看有没有 `imageinfo`：生效的重定向，接口也会返回目标文件的 `imageinfo`。
  要比较 `imageinfo.url` 末尾的文件名和标题自己的文件名，相同才是自己的文件。`image_files.py` 实现了这个判断，分五类：
  `free`（页面不存在）、`own_file`（自己的文件）、`broken`（自己的文件，页面文本却是 `#REDIRECT`，重定向没生效）、
  `redirect`（重定向页，已生效或目标没有文件）、`plain_page`（只剩描述页）。
- `image_redirect.py`：创建前先查标题，`own_file`/`broken` 默认跳过并汇总为 `CONFLICT`，加 `--delete-existing` 才处理：先删除文件页（连同文件），再只恢复页面文字的各个修订、文件保持删除，最后（文本不对才）写重定向。这样页面 ID 和修订历史保持不变，页面上能看到 delete/undelete 日志。每个标题 3 次请求（删除、列出已删修订、恢复）。
- `image_upload.py`：上传前先查标题，`redirect`/`broken` 默认跳过并汇总为 `CONFLICT`，加 `--delete-existing` 才先删后传。
- `image_audit.py`：只读，不登录；上传名字和重定向标题各按类别汇总，`--output` 写出每个类别的清单。
- `image_redirect.py --recheck`：不看状态库的“已上传”记录，按线上实际状态核对计划里的全部重定向：已生效且目标对的跳过，
  没生效或目标不对的补建（有自己文件的要同时加 `--delete-existing`）。早年写下但没生效的重定向在状态库里是“已上传”，只有这个模式会重做。
- 安排顺序：先审计，再清理冲突，上传图片，最后建重定向，收尾再审计一遍。不要先上传再回头删，上传占住的名字之后建不了重定向。
- 历史遗留：很久以前有人用中文名传过图片的名字、早年批量写下但没生效的重定向，都属于这一类，需要删了重建。

### 重传判定（image_check.py）

`image_inventory.py` 记下每张图的本地文件 sha1 后，对“待更新”的图标查询线上同名文件，按下列规则决定是否重传。
`image_upload.py` 上传前会再核对一次，所以未变化的图片不会反复上传。加 `--offline` 可跳过线上查询。

1. 本地与线上文件 sha1 相同：不传。
2. 黑名单 `config/image_unstable.json`（多次导出仍有波动的图标，名字不带扩展名，PNG 与 WebP 通用）：线上已有且像素尺寸相同的不更新；
   其余 `image_upload.py` 默认拒绝上传（线上没有的也不传，并列出这些缺失的图），加 `--force-unstable` 才上传。
   线上尺寸与本地不同时黑名单不起作用，照常上传。
3. 视觉规则：两张图缩到 32×32（BOX 平均、按 alpha 预乘）后逐像素取 RGBA 通道差的最大值，
   平均 ≤ 1 且最大 ≤ 32 视为相同：不传。不同显卡、驱动下方块边缘的光栅化差异属于这一类。
   动画（`*_dynamic.webp`，legacy 为帧条 `*_dynamic.png`）**逐帧**套用这条规则，任一帧超限即视为不同；
   尺寸、帧数或时长变化直接视为不同。
4. 其余上传，包括线上没有的图片。

动图没有单独的免更新规则。数据页里的帧数、帧尺寸来自本次导出，线上帧条必须与之一致，
所以更新数据页时应一并上传图片（上传图片仍须用户明确说明）。

视觉判定按（线上 sha1，本地 sha1）存进状态库的 `visual_checks` 表，同一对不再比对；
下载的线上原图缓存在 `source_state/wiki-originals/`（不提交）。

`config/image_unstable.json` 是图片黑名单：同一整合包、同一导出器多次导出，像素仍然不同的图标
（Dumper 已固定渲染时钟、动画贴图、模组动画计数器和实体随机数，名单只收剩下的）。用多次导出的 `icon-hashes.json` 生成：

```bash
python image_check.py stability <第一次的 dumps> <第二次的 dumps> [更多...]   # 并入现有名单
python image_check.py stability <dumpsA> <dumpsB> --replace                 # 导出器修好一批后，用本次结果重建名单
```

从 PNG 换到 WebP 的第一次发布时，黑名单里的图标在线上还没有 `.webp` 文件，默认会被拒绝上传、页面上显示不出；
这一次要给 `image_upload.py` 加 `--force-unstable`（线上已有且尺寸相同的黑名单图标不会因此重传）。

`icon-hashes.json` 由 GTNH-Dumper 写出，记录每张图的像素哈希；导出器已固定渲染时钟，
多数随时间变化的图标已经稳定，名单只收剩下的。

判定存在 `image_inventory.db` 中，随仓库提交，其他维护者拉取仓库后同样复用，不必重新下载比对。

### 中文重名图片的人工选择

物品预处理会自动扫描重名，生成诊断报告和重定向表。人工选择和默认规则确定的标题进入
重定向任务，其余标题留在候选报告中，供编辑者选择。
名称经过文件名清理、空格/下划线归一和首字母规范化之后发生的冲突也会被合并检查。

在本目录运行：

```bash
# 先生成最终物品 key、名称索引和重名报告。
python split_all.py items

# 生成候选报告，查看各名称对应的物品。
python item_names.py generate-config --output image-name-candidates.json

# 编辑 config/preferred-items.json 的明确约定后，重新应用选择。
python split_all.py items
```

默认读取 `config/preferred-items.json`（`schemaVersion: 2`）。标题按不带扩展名匹配，清单里的 `名称.png` 对 WebP 导出同样有效。每个标题的 `key` 是明确指定的
完整物品 key，优先于自动规则；显式 `null` 表示跳过该别名。未配置的名称自动使用默认偏好：
先优先选择 tooltip 中没有“弃用”“移除”或 `deprecated` 标记的物品，再按
Minecraft → 流体身份 `fluid.*` → GregTech → IC2 → 其他模组选择。在同一物品身份内优先无 NBT
版本，在同一注册物品内优先 damage 为 0 的版本。GT 矿石优先普通石头底岩；天然状态和
小型矿石属性仍保留区分。筛选后仍不能唯一确定的名称留在本地未解决报告中。
同一 rawKey 没有无 NBT 版本时，按完整 key 字典序选择第一个作为图片代表，配方继续使用各自的完整 key。
GT 电池箱按 tooltip 选择 1 槽版本。多个流体优先选择 `registeredBy=gregtech` 的候选；
此字段来自 Forge 注册表；该规则适用于包含此字段的数据。
同模组不同注册物品、没有零 damage 且无专门规则的多个子类型仍留给人工。
矿石规则适用于 GT `gt.blockores2`～`gt.blockores7`。

需要先为剩余冲突生成一版可编辑的默认清单时，使用同次导出的 `itemicon.json`：

```bash
python item_names.py generate-preferred --items-dir <预处理的Items目录> \
  --icon-map <导出目录>/itemicon.json --output preferred-items.generated.json
```

该命令保留已有人工选择和显式 `null`，仅补充现有规则筛选后仍有多个候选的名称，新增条目只保存 `key`。
在剩余候选中，选择 `itemicon.json` 中最先出现的图标；同图标的多个身份按完整 key 排序。
这与 Dumper 为中文重名依次分配无编号、` 2`、` 3` 别名时使用的遍历顺序一致，
不代表物品优劣，且可能随模组版本、注册顺序和配方变体变化。生成结果固定保存完整 key。
规则已唯一确定的名称继续使用自动规则；缺少候选图标映射时停止生成，需核对数据来源。
输出是独立的 `schemaVersion: 2` 清单，不覆盖公共首选文件，也不会自动应用。
可检查生成清单后通过 `IMAGE_NAME_CONFIG` 指定它，再重新预处理。

`null` 表示不发布该中文图片重定向。下面使用演示数据：

```json
{
  "schemaVersion": 2,
  "choices": {
    "示例物品.png": {"key": "example:item:3", "reason": "编辑者确认的显示约定"},
    "不发布的别名.png": {"key": null, "reason": "不为该名称发布图片别名"}
  }
}
```

公共清单保存已采用的选择，通过 PR 修改 `key`；`reason` 是可选的人工说明。
当前清单包含 2.9.0-RC-2 本体与附属模组快照中按 Dumper 顺序补齐的默认选择，自动生成的条目只保存 `key`。
本体与附属共用这份清单。例如“引力透镜”明确选择 GT 物品；独立处理 TST 时，
所选 GT key 不在候选中，该中文别名会记录到失效选择报告并跳过，TST 图标仍按完整物品身份处理。
`IMAGE_NAME_CONFIG` 可显式指定同格式的自定义清单，替代默认公共清单。
候选列表写入本地 `image-name-candidates.json`，冲突和失效选择写入
`image-name-resolution.json`。这两个文件供本地检查，公共 PR 修改 `config/preferred-items.json`。
失效 key 或已消失名称会报告并跳过，请在报告中核对并更新首选配置。
配置、原物品数据或图标映射变更后必须重新预处理；图片发布前会验证当前配置路径和文件的 SHA-256，切换配置路径后也须重新预处理。

图片状态使用 `images` 与 `redirects` 两张本地表，支持多个中文别名指向同一个图片。
首次运行图片清单时会迁移旧表中的上传状态。图片清单及上传脚本负责维护该数据库。
旧表中同一个中文标题关联了多张图片时，该标题重新标为待更新，按当前人工选择纠正目标。

### 中文名称查询索引

`split_all.py` 处理 `items`、`oredict` 或 `recipes` 后，依据最终物品、矿辞和配方刷新
`ItemNames/<桶号>/<分块号>.json`。名称按本模组、本版本的索引字节量选择桶数，页内保留实际 `bucketCount`。
以下为结构示例：

```json
{
  "bucketCount": 64,
  "names": {
    "示例容器": {
      "keys": ["example:container", "example:container_nbt"],
      "rawKeys": ["example:container"],
      "sourceHandlers": ["example/FillingHandler"],
      "usageHandlers": [
        "example/EmptyingHandler",
        ["minecraft/ShapedRecipeHandler_crafting", [0, 3, 12]]
      ]
    }
  }
}
```

`names[中文名]` 收纳该名称的全部查询信息：

- `keys`：最终完整 key 数组，保留同名候选和 NBT 变体。
- `rawKeys`：去重的基础身份数组，包含注册名和 damage，用于配方输出分桶及单产物查询的变体合并。与 `keys` 相同时省略。
- `beeIdentities`：蜂类物品才有的 `{"完整key":{"beeSpecies":蜂种uid,"beeType":类型}}` 映射。
- `sourceHandlers`：能获得该名称物品的处理器列表。
- `usageHandlers`：用途条目列表；字符串表示整个处理器，`[handler, [编号...]]` 表示该输入名称涉及的桶号或普通文件号。

handler 使用 `Recipes` 下的完整 `mod/handler` 路径。同名候选的路径取并集，包含矿辞和语义身份对应的配方。
来源列表同时收集基础身份的产物，供单产物名称查询合并 NBT 变体。
`otherItems` 同时计入来源和用途；专用掉落集合计入来源，`keyItem` 计入用途。空列表省略。
图片的首选候选由图片配置单独决定。

输入编号在配方最终拆分后生成，复用同一种二元数组结构。版本配置 `storage.recipeBuckets` 中的处理器将编号解释为桶号，其余处理器解释为普通文件号。
分桶索引启用有序合成、匠魂弩箭固化、组装机、合金炉回收和锻造锤；桶号 0 对应目录 `00/`。
普通文件索引启用伪装板合成、基因采样机、电弧炉回收、组装机、流体提取机回收、研磨机、研磨机回收、微光嬗变和包含要素的物品；文件号 0 对应 `0.json`。
普通分页中，单文件处理器以及覆盖全部文件的名称保存字符串条目，其余保存所涉及的文件号。
同名物品、矿辞和语义身份的编号取并集，多输入查询取交集；分桶时继续与输出定位的桶集合取交集。
锻造锤的 `shared/` 页随数字桶一起读取；`[handler, []]` 表示该名称仅涉及 shared 配方。
桶内全部分页参与查询；普通文件号交集为空时返回空结果。名称索引、最终配方文件和版本配置同步更新。

同一名称可以跨页，各页数组和 `beeIdentities` 映射合并后再使用；若合并后的记录没有 `rawKeys`，则采用 `keys`。
默认每页最多 1,800,000 字节，最终检查计入所有字段和标点的 UTF-8 字节数，硬上限为 2,000,000 字节。
Lua 查询一次读取命中名称桶的所有分页，使用 `$objectToArray` 按名称字面值筛选，支持名称中的点号和 `$`。
精确名称转换同时取得 handler；多个输入的用途列表与多个输出的来源列表按版本取交集，填写 `type` 时再与指定处理器范围相交。
另查 Fluids 得到的候选、模糊名称和 `@key` 使用各自的查询条件。各版本使用覆盖身份和矿辞候选并集的名称参与 handler 交集。

### 物品 key 分桶

`split_all.py items` 生成 `Items/<桶号>/<分块号>.json`。它以最终 `key` 分桶，
保存每个物品的完整记录，包括名称、说明、图片映射、NBT 和原 `index`。
原 `Items/` 根目录数字顺序分页在生成成功后清理，只保留一份物品数据。
属于矿辞的物品带 `oredict` 列表（如 `"oredict": ["ingotSteel", "ingotAnySteel"]`，顺序同导出）：由 `oreDictionary.json`
反向写回，成员解析与 `OreDictionary/` 页相同（`:32767` 通配展开为已导出的子类型），两边一致；不属于任何矿辞的物品没有该字段。
有关联的物品还带 `researchKeys`（魔导手册研究 Key）与 `questIds`（任务 ID），规则与线上条目页的查询相同，没有关联则不带：
研究——非虚拟、有分类的研究，以该物品为图标（没有贴图资源时）、物品扫描条件或页面（配方）产物；物品的 `key` 与 `rawKey`
按 meta 规则扩展候选（非零 meta 匹配自身与 `:32767`，无 meta 或 `:0` 匹配 base、base:0、base:32767），见 模块:Thaumonomicon 的 related。
任务——按 BetterQuesting 的匹配规则，不再把堆栈挂到同一 `id:Damage` 的全部 NBT 变体上（实现见 `quest_ids_by_item`）：
图标与奖励只关联堆栈本身（`betterquesting/itemKeys.json` 给出的精确 key，没有时为 `id` 加非零 `:Damage`）；任务要求关联
`itemKeys.json` 的 `taskMatches`，即 Dumper 用 BQ 的 `StackMatch`（按任务的 `ignoreNBT` / `partialMatch`）算出的、任务实际接受的物品——
要求带 NBT 且不忽略 NBT 时全部关联（如指定品种的蜜蜂），否则匹配超过 `GENERIC_MATCH_LIMIT`（16）个时只关联不带 NBT 的基础物品
（如“任意量筒”）；流体要求为 `fluid.名称`。物品按自己的 `key` / `shortKey` 匹配，合并成流体的展示物品另按 `rawKey` 匹配。
另有 `achievements`（成就 ID）与 `trophies`（奖杯 ID）：成就、奖杯的 `items` 与物品 `key` 相等即关联，不带 NBT 的写法还关联同一 `rawKey` 的 NBT 变体（另认 `:32767` 通配），
规则见上文“成就与奖杯”。这些都只在 `MOD_LOCAL_NAME=GTNH` 时生成，输入为同一导出的 `tc4thaumonomicon.json`、`betterquesting/` 与 `achievements.json`。查询端取得 key 后计算对应桶，再在桶内精确匹配 key；
同一桶超过大小限制时需要读取该桶的所有分块。

### 可选配方分桶

```bash
# 先完成物品处理；配方内部先做 NBT 关联，再生成可选分桶。
python split_all.py items recipes --recipe-buckets
```

启用后，适配池原位写到 `Recipes/<原机器目录>/<桶号>/<分块号>.json`，成功生成清单后删除对应机器根目录的旧数字顺序页。
未启用分桶或不在适配范围的配方池仍使用 `Recipes/<原机器目录>/<分块号>.json`。
范围固定为有序合成、无序合成、匠魂弩箭固化、匠魂部件压模、组装机、合金炉回收、流体固化器、锻造锤
这八个主要 handler，并且只在当前来源已拆成多个文件时处理。其他 handler 保留原文件。
输入为关联完成后的产物 `rawKey`，缺省时使用产物 `key`；同一注册名和 damage 的 NBT 变体共用一个逻辑桶。
完整 `key`、NBT 和蜂类的 `beeSpecies`、`beeType` 原样保存在配方中，供精确匹配与显示使用。
单输出槽允许物品或流体；嵌套候选须归约为同一个路由身份。
锻造锤的多输出配方单独写入该 handler 的 `shared/<页>.json`；查询锻造锤时同时读取目标桶与 shared 目录的全部数字分块。
其余七个 handler 若出现多输出、`otherItems` 或无可识别输出，整池保留原文件，诊断写入 manifest。
每条配方写入一个桶。同槽候选的完整 key 可以不同，只要其 `rawKey` 相同即可归同桶；
存在多个路由身份或没有可识别身份时，整池保留顺序分页。
记录保留原 `index`，配方身份由 `(handler, index)` 确定。
`Recipes/routing-manifest.json` 的 `writtenRecipes` 记录实际写入条数，并列出处理的 handler、原文件、分桶文件和共享文件，便于读取端定位。读取配方时遍历清单指向的数据文件。
各 handler 按当前模组、当前版本的实际数据量选择桶数。小附属的组装机与 GTNH 的组装机可以采用不同桶数；少于两个来源文件时保留顺序分页。

每个配方页的 `routing.bucketCount` 和清单的 `handlers[handler].bucketCount` 标明该池桶数。
`routing.identityFields` 为 `["rawKey", "key"]`，表示按此顺序取首个非空字段作为路由身份。
清单的 `recipeBuckets` 为 `{handler: 桶数}`，只包含本次实际生成的池；读取端用此表同步路由配置。

重新生成分桶时，从原始 dumps 运行 `split_all.py`。分桶器接受顺序分页的配方文件。

查询端读取目标桶后，按原有条件匹配、排序和分页；未指定产物时需遍历该配方池的全部数据文件。
当前 Lua 先按数据页路径取候选，再对候选排序。重新分桶会改变文件路径，因此默认首条和分页顺序可能变化；完整匹配结果保留。

处理物品、矿辞或配方后，`source_state/<本地模组>/<版本>/version-config.lua` 从最终页面读取实际桶数，生成可发布到 `模块:Database/Versions/<远端模组>/<版本>` 的配置。数据页和此版本配置一同更新，并在 `模块:Database/Versions` 的 `versionModules` 中登记该模块。

### 分块哈希约定

名称索引以中文名为输入，物品分桶以完整 `key` 为输入，配方分桶以产物 `rawKey`（缺省时为 `key`）为输入。
三者共用 `routing_hash.bucket_id(text, bucket_count)`，调用时传入当前数据集或 handler 的实际桶数。JSON/TABX 页的 `bucketCount` 标明实际桶数，哈希算法不变：

1. 对输入的 UTF-8 字节计算 SHA-256。
2. 前后各 16 字节逐字节异或。
3. 按 Dumper 的 `0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ` 字符表，保留低 15 位 Base62，左侧补零。
4. 将该 15 位值对当前配置的桶数取模。桶号至少两位，例如 `00`、`63`、`100`、`127`。

前三步与当前 Dumper 的 NBT 后缀算法一致，测试包含现有 `{}` → `gLuB0gegBqh1aRe` 向量。
对于 16、32、64 或 128 桶，桶号也等于
`(digest[15] XOR digest[31]) % bucketCount`（字节从 0 计数），因此无需在 Lua 中用浮点数表示完整哈希整数。

## 独立 mod 版本数据

在 `.env` 中分别配置本地目录名、远端目录名和版本号，例如上文的
`MOD_LOCAL_NAME=TwistSpaceTechnology`、`MOD_REMOTE_NAME=TST`、`MOD_VERSION=1.0.0`。
然后在仓库的 `databaseDump` 目录依次运行：

```bash
python split_all.py
python wiki_inventory.py
python data_upload.py
```

来源为 `dumps/filtered/TwistSpaceTechnology/`，配方目标例如
`Data:Versions/TST/1.0.0/Recipes/gregtech/GTNEIDefaultHandler_assembler/0.json`。
修改配置后重新运行拆分和清单。页面清单和上传任务范围使用远端名及版本号。

仅处理原拆分器支持的数据类型。缺失或零字节的输入会提示跳过，CSV 清单和筛选报告
不生成数据页。物品所需的 `itemicon.json`、`fluid.json` 优先取本地模组目录，缺失时
从完整 dumps 读取作为关联数据。

### 分桶文件大小限制

`Items`、`ItemNames` 和每个已适配配方池分别统计紧凑 JSON 的 UTF-8 数据字节数，按目标大小向上取整，再取不小于该值的最小 2 的幂作为桶数：1、2、4、8……。例如目标为 1,800,000 字节时，10 万字节用 1 桶，500 万字节用 4 桶。TABX 按最终行数据统计；配方的 shared 记录独立分页。
桶数控制平均数据量；哈希分布不均、页头和单条记录大小由桶内分页处理。每个桶的所有数字分页都参与查询。
默认分页上限为 **1,800,000 字节**；可调参数 `max_bytes` 必须为正整数且不得超过硬上限
**2,000,000 字节**（十进制 2 MB）。所有页面按最终紧凑 JSON 的 UTF-8 字节数分页，
计入中文、emoji、metadata、括号和逗号；写入前再次检查完整字节长度。
选择桶数时累计各条记录的编码长度；分页时逐条编码和累计，避免反复序列化整批。

名称索引的 `names[名称]` 长数组可跨文件分页，读取端应合并各页同名数组。
单条物品、配方、名称或 key 超出一页容量时，生成停止并报告错误。
`Recipes/routing-manifest.json` 同样受 2,000,000 字节硬上限约束，超限时生成失败。
生成失败后应修正数据或分页参数并重新生成成功，再执行清单和上传步骤。
