# Ball · 足球 & NBA 数据采集 · 预测 · 邮箱推送

一个 Python 项目，包含三大模块：

- **爬虫模块 (`ball/crawler`)**：基于 ESPN 公共站点 API（无需密钥）采集
  足球顶级联赛（英超 / 西甲 / 意甲 / 德甲 / 法甲）与 NBA 的
  **赛程、比赛详情、球队、球员、伤病** 数据，写入数据库。
- **数据库 (`ball/db`)**：SQLAlchemy 模型，SQLite 默认（可换 PostgreSQL）。
- **深度学习模块 (`ball/dl`)**：从历史赛果构造球队近期状态特征，训练
  MLP 分类模型预测赛果（胜/平/负），对新赛程进行预测。
- **邮箱推送 (`ball/notifier`)**：把预测报告整理成现代化 HTML 邮件，
  经 SMTP（默认 163 邮箱）发送到指定收件人。
- **体彩竞猜 (`ball/sporttery`)**：从竞彩网官方 API 抓取每周足球/篮球
  **竞猜赛程与固定奖金**，通过桥接表（联赛/队名）匹配到本地 ESPN
  赛程，复用预测模型得出结果并邮件推送。联赛不固定，取决于官方每周开售。

---

## 1. 安装

```bash
cd d:/wenlin/ball
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # 按需填写邮箱 SMTP 授权码
```

> 深度学习依赖 `torch`（体积较大）。若只跑爬虫，可先不装 torch：
> `pip install requests pyyaml python-dotenv sqlalchemy pandas numpy schedule`

---

## 2. 快速开始

```bash
# 初始化数据库表
python main.py init

# 爬取英超 2024 赛季（会一并同步球队/球员/伤病/赛程）
python main.py crawl --sport football --league eng.1 --season 2024

# 爬取 NBA（不传 --league 时取配置里的第一个）
python main.py crawl --sport nba

# 训练英超预测模型
python main.py train --sport football --league eng.1

# 预测英超未来 3 天赛程（结果写入 predictions 表）
python main.py predict --sport football --league eng.1

# 推送预测报告（经邮箱发送，详见 config.yaml 的 notify.email 段）
python main.py notify --sport football --league eng.1 --name "英超"

# 体彩竞猜：抓取官方赛程 -> 匹配本地 ESPN 赛程 -> 预测 -> 邮件
#   --sync         先抓取竞彩涉及联赛的 ESPN 近期赛程（便于匹配上 upcoming）
#   --train-missing 对缺模型的竞彩联赛尝试训练（需该联赛已有≥50场历史）
python main.py sporttery --notify --sync --train-missing

# 一步到位：爬取 + 训练 + 预测 + 推送
python main.py run --sport football --league eng.1 --season 2024 --train --notify

# 对所有配置的联赛跑完整流程
python main.py run-all --season 2024 --train

# 后台定时爬取（默认每 24 小时）
python main.py schedule --every 24
```

---

## 3. 数据结构（数据库表）

| 表 | 说明 |
|----|------|
| `leagues` | 联赛（code 如 `eng.1` / `nba`） |
| `teams` | 球队（含近期累计战绩字段） |
| `players` | 球员（位置、号码、头像） |
| `matches` | 赛程与赛果（主客队、比分、状态、场馆） |
| `match_details` | 比赛详情（事件/阵容/统计的原始 JSON） |
| `injuries` | 伤病（球员、球队、伤型、状态） |
| `predictions` | 模型预测（各类概率、标签、置信度） |

---

## 4. 关于「邮箱」推送

本项目通过 **SMTP** 把预测报告以现代化 HTML 邮件发送到指定收件人
（默认使用 163 邮箱作为发件箱）。配置见 `config.yaml` 的 `notify.email` 段：

```yaml
notify:
  channel: "email"
  enabled: true
  email:
    smtp_host: "smtp.163.com"
    smtp_port: 465
    use_ssl: true
    username: "wenlin_x@163.com"
    password: "${EMAIL_AUTH_CODE}"   # 163 客户端授权码，填在 .env
    sender_name: "Ball 预测系统"
    sender: "wenlin_x@163.com"
    recipients:
      - "wenlinxie@foxmail.com"      # 收件人
    subject_prefix: "Ball 预测"
```

- `password` 是 163 邮箱的**客户端授权码**（非登录密码），写在 `.env`
  的 `EMAIL_AUTH_CODE` 中（`.env` 已被 gitignore，不会入库）。
- 邮件采用响应式 HTML 模板：渐变标题栏 + 每场预测卡片（含主胜/平/客胜
  概率进度条与置信度），在 Foxmail、Gmail 等主流客户端均有良好显示。
- 若 SMTP 发送失败，会自动回退到「本地保存报告到 `data/reports/` +
  控制台打印」，结果不会丢失。

---

## 5. 体彩竞猜预测

把「竞彩网每周开售的足球/篮球竞猜」与本地 ESPN 赛程打通并预测：

```bash
python main.py sporttery --notify --sync --train-missing
```

- **数据来源**：竞彩网官方接口 `webapi.sporttery.cn/.../getMatchCalculatorV1.qry`
  （足球 + 篮球）。每场含中文队名、联赛名、北京时间、以及各玩法
  固定奖金（足球：胜平负 / 让球 / 总进球 / 比分 / 半全场；
  篮球：胜负 / 让分 / 大小分）。
- **匹配机制**（`ball/sporttery/mapping.py`）：体彩的联赛/队名是中文，
  与 ESPN 的英文不一致，故用**可扩展**桥接表转换：
  - `LEAGUE_MAP`：竞彩联赛名 → `(sport, espn_league_code)`。已覆盖竞彩常见的
    **数十个联赛与杯赛**：五大联赛、荷/葡/苏/比/土/俄/瑞士/奥/希腊/北欧、
    巴/阿/墨/美职/日职/澳超/沙特/中超，欧冠/欧联/欧协联、各国内杯赛、
    以及世界杯/欧洲杯/美洲杯/亚洲杯/欧国联/世预赛/友谊赛等国家队赛事，
    外加 NBA/WNBA。所有 code 均对 ESPN API 实测有效。
  - `TEAM_ALIASES`：俱乐部中文队名 → `(sport, code, espn_team_name)`，英文侧
    取自 ESPN 实测 `displayName`。
  - `NATIONAL_TEAMS`：国家队单列一张**共享表**，凡国家队赛事（世界杯/世预赛/
    欧洲杯/欧国联/美洲杯/友谊赛…）都用它解析，无需逐赛事重复维护。
  匹配键 = 联赛 + 比赛日期（北京时间转 UTC，±1 天容差）+ 双队名；队名比较
  会**折叠重音/标点并做子串容错**（如 `Bournemouth`↔`AFC Bournemouth`、
  `湖人`↔`Los Angeles Lakers`），且允许主客颠倒，鲁棒性强。
- **预测**：匹配到的场次若有该联赛的已训练模型，则输出主胜/平/客胜
  概率与置信度；缺模型时报告标注「暂无训练模型（可加 `--train-missing`）」。
- **邮件**：经既有 SMTP 通道发送「体彩竞猜预测」HTML 报告，
  每场展示对阵、北京时间、固定奖金与模型预测；未匹配项透明列出原因
  （未知联赛 / 队名未映射 / 本地缺赛程），便于补全桥接表。
- **`--sync`**：抓取竞彩涉及联赛的 ESPN 近期赛程，使 upcoming 场次
  进入 `matches` 表以便匹配。
- **`--train-missing`**：对匹配到但缺模型的联赛尝试训练
  （需该联赛在库已有 ≥50 场已结束比赛，否则自动跳过）。

> 联赛不固定：体彩每周开售哪些联赛取决于官方。本功能对未覆盖的
> 联赛**不会报错**，只在报告中列出，往 `mapping.py` 两张表追加即可扩展。
> 预测结果仅供参考，不构成任何投注建议。

---

## 6. 配置

所有可调项见 `config.yaml`：数据库地址、爬取节奏（延迟/重试）、
深度学习超参、邮箱推送、流程参数（前瞻天数等）。`.env` 中的变量会覆盖
配置里的 `${VAR}` 占位符。

---

## 7. 目录结构

```
ball/
├── main.py                 # CLI 入口
├── config.yaml             # 配置
├── requirements.txt
├── ball/
│   ├── config.py          # 配置加载
│   ├── db/                # 引擎 + 模型
│   ├── crawler/           # ESPN 爬虫（football / nba / scheduler）
│   ├── dl/                # 特征 / 模型 / 训练 / 预测
│   ├── notifier/         # 邮箱推送（SMTP + HTML 模板）
│   ├── report.py          # 预测报告文本格式化
│   ├── sporttery/        # 体彩竞猜：抓取 + 桥接匹配 + 预测报告
│   └── pipeline.py        # 流程编排
└── data/                  # 数据库与模型（运行时生成）
```

---

## 8. 说明与局限

- 数据来自 ESPN 公共站点 API，免费但可能随时变动；请合理设置爬取频率。
- 预测模型基于球队近期战绩等基础特征，作为技术演示；
  **预测结果仅供参考，不构成任何投注建议**。
- 伤病、阵容等字段依赖 ESPN 接口返回，缺失时自动跳过。
