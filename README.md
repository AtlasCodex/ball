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

## 5. 配置

所有可调项见 `config.yaml`：数据库地址、爬取节奏（延迟/重试）、
深度学习超参、邮箱推送、流程参数（前瞻天数等）。`.env` 中的变量会覆盖
配置里的 `${VAR}` 占位符。

---

## 6. 目录结构

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
│   └── pipeline.py        # 流程编排
└── data/                  # 数据库与模型（运行时生成）
```

---

## 7. 说明与局限

- 数据来自 ESPN 公共站点 API，免费但可能随时变动；请合理设置爬取频率。
- 预测模型基于球队近期战绩等基础特征，作为技术演示；
  **预测结果仅供参考，不构成任何投注建议**。
- 伤病、阵容等字段依赖 ESPN 接口返回，缺失时自动跳过。
