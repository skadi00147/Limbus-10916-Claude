# 《边狱公司》蜘蛛巢 拇指 父辈 罗佳（10916）Claude 主题视觉替换 Mod

> **Limbus Company — Claude-Themed Rodion (`10916` Thumb Father) Visual Skin Pack**  
> **作者 / Author**: `skadi00147` | **适配游戏端**: PC Steam 官方客户端（Unity `6000.3.12f1`）

本仓库开源了 **「蜘蛛巢 拇指 父辈 罗佳（`10916`）」Claude 主题视觉外观资产**，并随包附带一套极简、开源透明的本地命令行安装与一键无损还原工具。  
*（注：本仓库**仅包含 `10916` 皮肤资产与极简 CLI 安装/还原器**；不包含任何内部创作端可视化校准 Studio UI。）*

---

## 🚀 懒人最推荐：复制一句话，让你的 AI 帮你全自动安装！

如果手头正在用 **Antigravity / Cursor / Claude Code / Windsurf / Codex** 等带终端执行能力的 AI 助手，**根本不用自己折腾命令行**——把下面对应场景的话复制发给 AI，它就会按 `AGENT_INSTALL.md` 自动查路径、建虚拟环境、备份原版并安全装好：

### 情形 A：你把 GitHub 仓库发给 AI
```text
请帮我克隆这个《边狱公司》10916 罗佳（Claude 主题）皮肤仓库到本地一个我方便保留的文件夹（不要放进临时目录），然后读取里面的 AGENT_INSTALL.md，按指引检查我的游戏路径、配置依赖并把皮肤安全装进游戏里。如果有需要我先开游戏缓存的步骤再叫我，装完告诉我怎么还原就行。
仓库地址：https://github.com/skadi00147/Limbus-10916-Claude.git
```

### 情形 B：你从网盘下载并解压了压缩包
```text
我已经把这个《边狱公司》10916 罗佳（Claude 主题）皮肤仓库下载/解压在当前文件夹了，请你读取里面的 AGENT_INSTALL.md，按指引帮我检查游戏目录、配好环境并一键装好皮肤。如果有需要我先开游戏缓存的步骤再叫我，装完告诉我结果以及以后怎么还原（记得提醒我保留这个文件夹）。
```

---

## 📦 皮肤包含内容与保持原味说明

### ✅ 已替换内容
* **战斗小人完整动作帧（39 帧 `standalone` 独立动作帧）**：基于该人格原生独立帧结构进行 Claude 主题重绘与锚点对齐，底层采用 `Full-Quad` 矩形扩边技术，确保衣摆与长发完整展示不被原版贴图边界裁切（`dead.png` 阵亡帧保留原版）。
* **战斗待机呼吸 Spine（`thumb_father_rodion_idle`）**：包含 `.atlas`、`.json` 与直通 Alpha 贴图，安装器会在打包时依据 `pma:true` 自动完成预乘 Alpha 转换。
* **一破立绘四件套（`10916_normal` 全家桶）**：包含 `1920×1080` 大立绘（`10916_normal.png`）、详情页窄卡（`10916_normal_info.png`）、编队方头像（`10916_normal_profile.png`）与助战长条（`10916_normal_profile_support_personality.png`），人眼中心与原生斜切遮罩均已精细对齐。

### 🕊️ 刻意留白（保持原版不变）
* **保留原著设定、配音与三破外观**：未替换三破动态立绘（`10916_gacksung*`）、阵亡帧（`dead.png`）、技能名称、技能图标、角色语音及人格故事文本（可在游戏内切回一破立绘外观展示完整新立绘；战斗小人与待机 Spine 全局生效）。

### ⚠️ 重要技术提示：共享词条 Tag 风险（Shared-Tag Hazard）
* 游戏本地化目录 `LimbusCompany_Data/Lang/<active>/UnitKeyword*.json` 中的 **`UnitKeyword_SPIDER_HOUSE`** 词条，是由所有「蜘蛛巢」系列人格（例如 `10415`、`10916` 等）**全局共享**的单一键值。
* 在纯官方客户端环境下，如果直接修改 `UnitKeyword_SPIDER_HOUSE`，会同时连带覆盖其他所有蜘蛛巢人格的 Tag 显示（例如把 `10415` 等人格也一并改掉）。为避免跨人格污染，本皮肤包默认**刻意留空且不修改任何词条 Tag**。
* 如后续开发者或 AI Agent 明确希望在本地测试词条/技能名覆写，可自行阅读 [`installer/lang_patcher.py`](installer/lang_patcher.py) 的实现机制，并自行承担全局共享词条联动变更的风险。

---

## 🎥 实机演示与多渠道下载

* **实机战斗演示视频（B 站）**：[`BV1R3Wk6MEiR`](https://www.bilibili.com/video/BV1R3Wk6MEiR/)（`https://www.bilibili.com/video/BV1R3Wk6MEiR/`）
* **GitHub 仓库 / Release 直下**：[`skadi00147/Limbus-10916-Claude`](https://github.com/skadi00147/Limbus-10916-Claude)
* **夸克网盘分流（`Limbus-10916-Claude.7z`）**：[`https://pan.quark.cn/s/a1b9fe798cc3?pwd=J4Ec`](https://pan.quark.cn/s/a1b9fe798cc3?pwd=J4Ec)（提取码：`J4Ec`）
* **百度网盘分流（`Limbus-10916-Claude.7z`）**：[`https://pan.baidu.com/s/1-hXJFqldBgE6ippzz54Knw?pwd=qtnq`](https://pan.baidu.com/s/1-hXJFqldBgE6ippzz54Knw?pwd=qtnq)（提取码：`qtnq`）

---

## 🛠️ 人类手动安装 & 一键还原（3 步搞定）

### 安装前准备（必看）
1. **关闭《边狱公司》游戏客户端**（运行中文件会被占用锁死）。
2. **至少先在游戏里见过一次 `10916` 人格**：打开 Steam 版《边狱公司》，在图鉴 / 人格 / 编队 / 战斗界面加载过一次「蜘蛛巢 拇指 父辈 罗佳 (`10916`)」，确保官方 Addressables 缓存已下载到本机 `LocalLow`。
3. 电脑已安装 **Python 3.12 或更高版本**（安装时请勾选 **`Add python.exe to PATH`**）。

### 方式一：双击一键安装 / 还原
1. 双击根目录下的 **`install_skin.cmd`** —— 脚本会自动创建本地 `.venv` 虚拟环境、安装依赖（若默认 PyPI 超时会自动切换清华镜像）、备份你的纯净原版文件到 `.state/` 并注入皮肤。
2. 以后想卸载皮肤、或游戏迎来官方大版本更新前，**关闭游戏后双击 `restore_vanilla.cmd`** 即可 100% 无损恢复官方原版。

> ⚠️ **重要提醒**：**千万不要在还没还原的情况下直接删除整个 Mod 文件夹！** 你的纯净官方原版备份均保存在本目录的 `.state/` 文件夹中。

### 方式二：手动终端命令
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "UnityPy>=1.25.0" "lz4>=4.4.0" "Pillow>=12.0.0" "etcpak>=0.9.15" "astc-encoder-py>=0.1.12" "texture2ddecoder>=1.0.6"

# 检查本机路径与 10916 缓存就绪状态
.\.venv\Scripts\python.exe -X utf8 -m installer status

# 备份原版并一键注入皮肤
.\.venv\Scripts\python.exe -X utf8 -m installer apply

# 离线构建校验（仅导出到 build/，不触碰游戏真实缓存与 .state/）
.\.venv\Scripts\python.exe -X utf8 -m installer apply --build-only

# 随时无损还原回官方原版
.\.venv\Scripts\python.exe -X utf8 -m installer revert
```

*(如遇游戏安装在非标准路径导致自动探测不到，可将 `config.example.json` 复制为 `config.local.json`，并在 `"game_root"` 中填入你的游戏根目录路径。)*

---

## ⚖️ 重要声明与免责条款

1. **非官方二创与原作版权声明**  
   本项目及所含皮肤资产为粉丝基于游戏《Limbus Company（边狱公司）》制作的**非官方免费同人视觉替换模组**，与游戏开发商 **Project Moon** 没有任何从属、授权、赞助或合作关系。游戏原作角色设计、世界观、台词文本、游戏程序、商标及其他一切官方知识产权均归 **Project Moon** 所有。
2. **形象出处与第三方无关声明**  
   本皮肤中的 **Claude 娘化形象来源于 B 站 UP 主 `@ZipZipPipe` 的 Claude 二创 AI 娘化形象**，与 **Anthropic 公司**（及其旗下产品品牌）没有任何从属、授权、合作或关联关系。
3. **纯本地视觉替换与公平合规声明**  
   本模组**仅在玩家本地客户端替换指定人格的美术贴图与待机 Spine 资产**，绝不修改任何战斗数值、技能伤害、抽卡/经济系统或逻辑代码；**不使用 BepInEx、DLL 注入或内存修改器**，不篡改官方核心校验配置表，亦不拦截任何网络封包或绕过服务器校验。
4. **风险自担与官方更新提示**  
   使用任何非官方客户端资源替换均属于玩家个人自愿行为，请自行评估并承担全部风险（包括但不限于游戏热更新后资源哈希变更导致异常等）。建议每次游戏大版本更新前，先执行一次 `restore_vanilla.cmd` 还原为纯净客户端；若出现异常，也可随时通过 Steam「验证游戏文件的完整性」恢复。
5. **开源范围与非商业协议**  
   本项目整体（含 `installer/`、`*.cmd` 安装还原脚本以及 `mods/` 下全部图片、立绘与 Spine 贴图）统一遵循 [PolyForm Noncommercial License 1.0.0](LICENSE) 非商业协议（`Required Notice: Copyright (c) 2026 skadi00147`）。代码透明无联网暗门、不采集或上传任何本地账号与隐私路径；美术资产由作者 **`skadi00147`** 借助 AI 辅助工作流联合重绘、修型校色与拆件整合完成，**仅限同好免费下载交流与个人非商业娱乐使用**。**严禁任何形式的倒卖、商业用途、付费搬运、放入付费整合包或冒充原作者发布**；若基于本皮肤进行二次修改或再分发，请保留原作者署名与本免责全文。
6. **致谢**  
   感谢开源社区项目 [UnityPy](https://github.com/K0lb3/UnityPy) 提供的 Unity 资源解析与打包能力。
