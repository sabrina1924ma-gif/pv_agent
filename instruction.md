收到！信息量很足，现在可以把架构做得非常具体。让我重新整理一版**完整更新后的架构设计**，把所有新约束都纳进来。好，信息很全，现在来把更新后的完整架构画出来，再做逐层的细节补充说明。

---下面是发电量曲线数据的分级查询设计，这是这次新增的核心技术点：下面是 Redis + PostgreSQL 双写的会话记忆设计：---

## 基于答案更新的关键设计细节

### 1. 飞书机器人接入

飞书走的是 Event Callback 机制，不是 WebSocket，这里有一个关键的异步问题要处理：

```python
# feishu/handler.py
from feishu import FeishuClient

@app.post("/feishu/callback")
async def feishu_callback(req: Request):
    body = await req.json()
    # 飞书要求5秒内返回200，否则会重试
    # 必须立即返回，异步处理
    background_tasks.add_task(process_feishu_message, body)
    return {"code": 0}

async def process_feishu_message(body: dict):
    msg = body["event"]["message"]["content"]
    session_id = body["event"]["sender"]["sender_id"]["open_id"]
    
    result = await graph.ainvoke(
        {"user_input": msg, "session_id": session_id},
        config={"configurable": {"thread_id": session_id}}
    )
    
    # 回复飞书消息卡片（支持Markdown渲染）
    await feishu_client.send_card(
        receive_id=session_id,
        card=build_card(result["messages"][-1].content)
    )
```

飞书消息卡片可以渲染 Markdown，报告直接用卡片发就能显示表格、标题、加粗，不需要额外转换格式。

------

### 2. 多电站 / 多设备的数据隔离

数据库表需要加 `station_id` 字段，`AgentState` 里也需要带上：

```python
# 表结构补充
class Device(Base):
    station_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("stations.id"), index=True)
    # ...

class Station(Base):
    __tablename__ = "stations"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(50))
    capacity_mw: Mapped[Decimal] = mapped_column(DECIMAL(10, 2))

# AgentState 补充
class AgentState(TypedDict):
    # ...
    station_id: str | None   # None = 查询全部电站
    station_ids: list[str]   # 用于跨站汇总报告
```

意图路由 Prompt 里也要增加电站实体提取：

```python
# 示例：用户说"查一下华东三站的逆变器故障"
# 输出：{"intent": "check_fault", "entities": {"station_name": "华东三站", "device_type": "inverter"}}
# tool_executor 先把 station_name 解析成 station_id，再查 fault
```

------

### 3. 发电量 15min / 1h 曲线 — PostgreSQL 实现

```sql
-- 原始表启用 TimescaleDB 超表（可选，普通 PG 用 time_bucket 也行）
SELECT create_hypertable('power_logs', 'recorded_at');

-- 1小时聚合物化视图（自动刷新）
CREATE MATERIALIZED VIEW power_hourly AS
SELECT
    device_id,
    time_bucket('1 hour', recorded_at) AS bucket,
    SUM(power_kwh)          AS total_kwh,
    AVG(efficiency)         AS avg_efficiency,
    AVG(irradiance)         AS avg_irradiance,
    AVG(temperature)        AS avg_temp
FROM power_logs
GROUP BY device_id, bucket;

-- 日聚合视图
CREATE MATERIALIZED VIEW power_daily AS
SELECT
    device_id,
    time_bucket('1 day', recorded_at) AS bucket,
    SUM(power_kwh) AS total_kwh,
    AVG(efficiency) AS avg_efficiency
FROM power_logs
GROUP BY device_id, bucket;
```

工具里按粒度选视图：

```python
@tool
async def get_power_generation(
    device_id: str,
    start_date: str,
    end_date: str,
    granularity: str = "auto"
) -> dict:
    """查询发电量曲线，granularity: 15min/1h/daily/monthly/yearly/auto"""
    
    # auto 模式：按时间跨度自动选粒度
    span_days = (parse(end_date) - parse(start_date)).days
    if granularity == "auto":
        if span_days <= 3:    granularity = "15min"
        elif span_days <= 30: granularity = "1h"
        elif span_days <= 365: granularity = "daily"
        else:                  granularity = "monthly"
    
    view_map = {
        "15min":   "power_logs",
        "1h":      "power_hourly",
        "daily":   "power_daily",
        "monthly": "power_monthly",
        "yearly":  "power_yearly",
    }
    table = view_map[granularity]
    # ... 查询并返回 data_points[]
```

------

### 4. DeepSeek-v4 API接入

------

### 5. 报告格式输出

因为三种下游都要支持（飞书卡片、Web 页面、可能还需要 PDF），报告生成统一用 Markdown，在输出层做格式转换：

```python
# utils/report_renderer.py
import markdown
import pdfkit  # 或 weasyprint

class ReportRenderer:
    
    @staticmethod
    def to_markdown(content: str) -> str:
        return content  # 直接输出，飞书/Web 均可原生渲染
    
    @staticmethod
    def to_html(content: str) -> str:
        return markdown.markdown(
            content,
            extensions=["tables", "fenced_code", "toc"]
        )
    
    @staticmethod
    def to_pdf(content: str, output_path: str) -> bytes:
        html = ReportRenderer.to_html(content)
        # 注入品牌样式
        styled = f"<style>{REPORT_CSS}</style>{html}"
        pdfkit.from_string(styled, output_path)
        return open(output_path, "rb").read()
```

飞书侧发 Markdown 卡片，Web 侧前端直接用 `react-markdown` 渲染，需要下载时走 `/report/export?format=pdf` 接口转 PDF。

------

### 6. 项目目录更新（对应所有新变化）

```
pv_agent/
├── main.py
├── config.py
├── feishu/
│   ├── handler.py         # Event Callback 接收
│   ├── card_builder.py    # 消息卡片模板
│   └── client.py          # 飞书 API 封装
├── database/
│   ├── connection.py      # asyncpg + SQLAlchemy
│   ├── models.py          # Station 表新增
│   └── migrations/
├── repositories/
│   ├── station_repo.py    # 新增：电站管理
│   ├── device_repo.py
│   ├── power_repo.py      # 支持多视图查询
│   ├── fault_repo.py
│   └── chat_repo.py       # Redis + PG 双写
├── tools/
│   ├── device_tools.py    # 增加 station_id 过滤
│   ├── power_tools.py     # auto granularity 逻辑
│   ├── fault_tools.py
│   ├── station_tools.py   # 新增：多站汇总
│   └── report_tools.py    # 对接报告 Prompt
├── agent/
│   ├── state.py           # 增加 station_id / station_ids
│   ├── nodes.py
│   ├── graph.py
│   └── prompts.py         # 内嵌你提供的报告 Prompt
├── utils/
│   ├── report_renderer.py # MD / HTML / PDF 三路输出
│   ├── redis_client.py    # aioredis 封装
│   └── logger.py
└── docker-compose.yml     # postgres + redis + ollama
```

------

拿去面试，这套设计基本能做到"每问必有答，每问有设计理由"。如果面试官追问任何一块，比如飞书的 5 秒超时问题、TimescaleDB 为什么比普通 PG 强、Redis TTL 为什么是 24 小时——都可以说清楚。需要我继续展开某一块的代码细节吗？