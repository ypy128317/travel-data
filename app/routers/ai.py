"""AI 智能行程助手：结合库内真实商品数据，用通义千问生成结构化行程。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, MODEL_NAME
from ..database import fetch_all
from ..knowledge import KB

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _kb_city(city: str) -> str | None:
    """把前端城市名映射到知识库城市键（如'成都市'→'成都'）。"""
    if not city:
        return None
    for kb_city in KB.cities:
        if kb_city in city or city in kb_city:
            return kb_city
    return None


def _kb_context(city: str, query: str, top_k: int = 5) -> tuple[str, list[dict[str, Any]]]:
    """检索知识库，返回(拼好的文本, 引用来源列表)。失败时静默降级为空。"""
    kb_city = _kb_city(city)
    if not kb_city:
        return "", []
    try:
        # 行程类优先线路推荐，问答类全类型
        hits = KB.search(query, city=kb_city, top_k=top_k)
        if not hits:
            return "", []
        lines = []
        for h in hits:
            lines.append(f"【{h['contentType']}｜{h['title']}｜出处:{h['source']}】\n{h['text']}")
        sources = [
            {"title": h["title"], "contentType": h["contentType"], "source": h["source"]}
            for h in hits
        ]
        return "\n\n".join(lines), sources
    except Exception:
        return "", []


def _as_list(value: Any) -> list[Any]:
    """把可能是 JSON 字符串 / list / None 的 payload 统一成 list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [value]
    return [value]


# ==================== 实时天气注入（工具调用 / MCP 思路） ====================
# 免费公开天气 API（无需 Key）。国内服务器访问 api.open-meteo.com 常超时，
# 因此优先用 wttr.in（国内稳定、中文城市名直接可用），Open-Meteo 作为备选。
# 任何失败都静默降级为空，绝不让天气查询拖垮主行程生成流程。

# wttr.in 天气描述(英文) → 中文；值里含 rain/snow/storm 等关键词的视为恶劣天气
_BAD_WORDS = ("rain", "drizzle", "shower", "thunder", "storm", "snow", "sleet", "hail", "fog", "mist")
_WX_ZH = [
    ("sunny", "晴"), ("clear", "晴"), ("partly cloudy", "多云"),
    ("cloudy", "阴"), ("overcast", "阴"), ("patchy rain", "局部有雨"),
    ("light rain", "小雨"), ("moderate rain", "中雨"), ("heavy rain", "大雨"),
    ("rain", "有雨"), ("drizzle", "毛毛雨"), ("shower", "阵雨"),
    ("thunder", "雷阵雨"), ("snow", "有雪"), ("fog", "有雾"), ("mist", "薄雾"),
]


def _wx_zh(desc: str) -> tuple[str, bool]:
    d = (desc or "").strip()
    low = d.lower()
    bad = any(w in low for w in _BAD_WORDS)
    zh = d
    for key, val in _WX_ZH:
        if key in low:
            zh = val
            break
    return zh, bad


async def _fetch_wttr(city: str, days: int) -> dict[str, Any] | None:
    """主数据源：wttr.in，中文城市名直接可用。"""
    try:
        async with httpx.AsyncClient(timeout=9) as client:
            r = await client.get(
                f"https://wttr.in/{city}",
                params={"format": "j1", "lang": "zh"},
            )
            r.raise_for_status()
            data = r.json() or {}
            weather = data.get("weather") or []
            if not weather:
                return None
            out_days = []
            for day in weather[: max(1, min(int(days), 7))]:
                hourly = day.get("hourly") or []
                # 取当天出现过的天气描述，挑第一个恶劣天气作为代表
                bad_desc = None
                any_desc = None
                for h in hourly:
                    dd = ((h.get("weatherDesc") or [{}])[0].get("value") or "").strip()
                    if not dd:
                        continue
                    any_desc = any_desc or dd
                    _, bad = _wx_zh(dd)
                    if bad and not bad_desc:
                        bad_desc = dd
                raw = bad_desc or any_desc or "晴"
                zh, bad = _wx_zh(raw)
                tmin = day.get("mintempC")
                tmax = day.get("maxtempC")
                out_days.append({
                    "date": day.get("date"), "desc": zh, "bad": bad,
                    "tempMax": int(float(tmax)) if tmax not in (None, "") else None,
                    "tempMin": int(float(tmin)) if tmin not in (None, "") else None,
                })
            if not out_days:
                return None
            bad_days = [d["date"] for d in out_days if d["bad"]]
            return {
                "ok": True, "city": city, "source": "wttr.in 实时天气 API",
                "days": out_days, "badDays": bad_days,
            }
    except Exception:
        return None


async def _fetch_weather(city: str, days: int) -> dict[str, Any] | None:
    """拉取目的地逐日天气：wttr.in 优先，Open-Meteo 备选；失败返回 None。"""
    if not city:
        return None
    wx = await _fetch_wttr(city, days)
    if wx:
        return wx
    # 备选：Open-Meteo（部分网络环境可达）
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
            if not results:
                return None
            lat, lon = results[0]["latitude"], results[0]["longitude"]
            fc = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                    "timezone": "Asia/Shanghai", "forecast_days": max(1, min(int(days), 7)),
                },
            )
            fc.raise_for_status()
            daily = (fc.json() or {}).get("daily") or {}
            times = daily.get("time") or []
            codes = daily.get("weather_code") or []
            tmaxs = daily.get("temperature_2m_max") or []
            tmins = daily.get("temperature_2m_min") or []
            out_days = []
            for i, t in enumerate(times):
                code = int(codes[i]) if i < len(codes) else 0
                desc = "有雨/雪" if code >= 51 else ("阴" if code == 3 else "晴")
                bad = code >= 45
                out_days.append({
                    "date": t, "desc": desc, "bad": bad,
                    "tempMax": round(tmaxs[i]) if i < len(tmaxs) and tmaxs[i] is not None else None,
                    "tempMin": round(tmins[i]) if i < len(tmins) and tmins[i] is not None else None,
                })
            if not out_days:
                return None
            return {
                "ok": True, "city": city, "source": "Open-Meteo 实时天气 API",
                "days": out_days, "badDays": [d["date"] for d in out_days if d["bad"]],
            }
    except Exception:
        return None


def _weather_prompt(weather: dict[str, Any] | None) -> str:
    """把天气数据转成注入 prompt 的指令文本。"""
    if not weather or not weather.get("days"):
        return ""
    lines = [f"{d['date']} {d['desc']}" + (f" {d['tempMin']}~{d['tempMax']}℃" if d.get("tempMax") is not None else "") for d in weather["days"]]
    bad = weather.get("badDays") or []
    s = "【实时天气数据（工具调用获取，非静态知识库）】\n" + "\n".join(lines) + "\n"
    if bad:
        s += ("注意：以下日期有降雨/降雪/雾/雷暴等天气：" + "、".join(bad) +
              "。请把这些天的【户外景点】替换为【室内备选】（博物馆、美术馆、室内展馆、商圈、温泉、影院等），"
              "并在对应安排和小贴士里说明是因为天气原因做的调整。晴天则优先安排观景、登山、公园等户外项目。\n")
    else:
        s += "未来几天天气良好，可正常安排户外景点与观景项目，注意早晚温差。\n"
    return s


class PlanRequest(BaseModel):
    areaId: int = Field(default=2, description="目的地城市 area_id（level=2）")
    city: str = Field(default="北京", description="目的地城市名，仅用于提示词")
    days: int = Field(default=3, ge=1, le=7, description="行程天数")
    budget: float = Field(default=2000, gt=0, description="总预算（元）")
    preferences: str = Field(default="", description="偏好，如'历史文化、美食'")
    raw_input: str = Field(default="", description="用户原始一句话（用于隐式需求识别，如'带老人去三亚'）")


class ChatRequest(BaseModel):
    message: str = Field(description="用户消息")
    areaId: int = Field(default=2, description="关联城市 area_id")
    city: str = Field(default="", description="城市名（用于知识库检索）")


def _collect_products(area_id: int, limit: int = 6) -> dict[str, list[dict[str, Any]]]:
    """从库里检索该城市真实可售商品，作为 AI 的素材。"""
    result: dict[str, list[dict[str, Any]]] = {"hotels": [], "scenics": [], "transfers": []}
    # 酒店（房型级，可直接下单）：每个房型取未来可售最低价
    hotels = fetch_all(
        """
        SELECT rt.id AS refId, CONCAT(h.hotel_name,'-',rt.room_type_name) AS name,
               lp.price AS price, h.star_rating_code AS rating, h.address,
               h.facility_payload, rt.room_type_name
        FROM hotel_room_types rt
        JOIN hotels h ON h.id=rt.hotel_id AND h.status_code='active'
        JOIN (
            SELECT room_type_id, MIN(sale_price_amount) AS price
            FROM hotel_room_daily
            WHERE status_code='active' AND available_inventory>0 AND business_date>=CURDATE()
            GROUP BY room_type_id
        ) lp ON lp.room_type_id=rt.id
        WHERE h.area_id=%s
        ORDER BY lp.price ASC LIMIT %s
        """,
        (area_id, limit),
    )
    for h in hotels:
        result["hotels"].append(
            {
                "refId": h["refId"], "name": h["name"],
                "price": float(h["price"]), "rating": h.get("rating"),
                "tags": _as_list(h.get("facility_payload")), "address": h.get("address"),
                "type": "hotel",
            }
        )
    # 景点（票种级）：每个票种取未来可售最低价
    scenics = fetch_all(
        """
        SELECT t.id AS refId, CONCAT(s.scenic_name,'-',t.ticket_type_name) AS name,
               lp.price AS price, s.rating_code AS rating,
               s.tag_payload, s.address
        FROM scenic_ticket_types t
        JOIN scenic_spots s ON s.id=t.scenic_spot_id AND s.status_code='active'
        JOIN (
            SELECT ticket_type_id, MIN(sale_price_amount) AS price
            FROM scenic_ticket_daily
            WHERE status_code='active' AND available_inventory>0 AND business_date>=CURDATE()
            GROUP BY ticket_type_id
        ) lp ON lp.ticket_type_id=t.id
        WHERE s.area_id=%s
        ORDER BY lp.price ASC LIMIT %s
        """,
        (area_id, limit),
    )
    for s in scenics:
        result["scenics"].append(
            {
                "refId": s["refId"], "name": s["name"],
                "price": float(s["price"]), "rating": s.get("rating"),
                "tags": _as_list(s.get("tag_payload")), "address": s.get("address"),
                "type": "scenic",
            }
        )
    # 接送服务
    transfers = fetch_all(
        """
        SELECT id AS refId, service_name AS name, service_type_code, vehicle_type_code,
               passenger_capacity
        FROM transfer_services WHERE area_id=%s AND status_code='active'
        ORDER BY id LIMIT 4
        """,
        (area_id,),
    )
    for t in transfers:
        result["transfers"].append(
            {
                "refId": t["refId"], "name": t["name"], "price": 0,
                "tags": [t.get("service_type_code"), t.get("vehicle_type_code")],
                "type": "transfer",
            }
        )
    return result


def _local_infer_needs(req: "PlanRequest") -> list[dict[str, str]]:
    """本地关键词兜底：从原话/偏好里识别隐性需求（AI 不可用或漏识别时用）。"""
    text = f"{req.raw_input or ''} {req.preferences or ''}"
    rules = [
        (("老人", "长辈", "父母", "老年人", "带爸妈", "带老人"), "减少步行强度",
         "长辈同行，体力有限", "避免爬山与长距离换乘，每天打卡不超过 3 个景点，安排午休与接驳"),
        (("小孩", "儿童", "亲子", "带孩子", "带娃", "宝宝"), "亲子友好节奏",
         "带孩子出行需要照顾作息", "安排午休时段、亲子友好景点，避免过晚活动与重辣餐饮"),
        (("轮椅", "腿脚不便", "行动不便", "拄拐"), "无障碍通行",
         "行动不便需无障碍设施", "优先无障碍景点、少台阶，优先安排接驳车"),
        (("预算紧", "省钱", "学生", "穷游", "性价比", "便宜"), "性价比优先",
         "预算有限要花在刀刃上", "优先免费/低价景点与性价比酒店，避开高价项目"),
        (("怕爬山", "不想走太多", "轻松", "慢节奏", "休闲"), "轻松慢节奏",
         "用户偏好轻松不赶路", "减少每日打卡数量，优先平地景点，留足自由活动时间"),
        (("拍照", "摄影", "出片", "打卡"), "观景出片时段",
         "用户重视拍照体验", "安排观景平台，把观景项目放在光线好的时段"),
    ]
    out: list[dict[str, str]] = []
    for words, need, reason, adjusted in rules:
        hit = next((w for w in words if w in text), None)
        if hit:
            out.append({"need": need, "from": f'识别到「{hit}」', "reason": reason, "adjusted": adjusted})
        if len(out) >= 3:
            break
    return out


def _fallback_plan(req: PlanRequest, products: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """无 Key / 调用失败时的本地降级行程，保证演示不中断。"""
    hotels = products["hotels"][:1]
    scenics = products["scenics"]
    days_plan = []
    for d in range(1, req.days + 1):
        spot = scenics[(d - 1) % len(scenics)] if scenics else None
        days_plan.append(
            {
                "day": d,
                "title": f"第{d}天 · {'探索城市' if not spot else spot['name'].split('-')[0]}",
                "morning": "酒店早餐后出发，感受当地晨间氛围" if hotels else "自由活动",
                "afternoon": f"游览 {spot['name']}（¥{spot['price']:.0f}）" if spot else "城市漫步",
                "evening": "品尝当地特色美食，早点休息",
                "recommendations": ([spot] if spot else []) + (hotels if d == 1 else []),
            }
        )
    return {
        "city": req.city, "days": req.days, "budget": req.budget,
        "summary": f"为您规划{req.city}{req.days}日游（本地推荐模式）。",
        "days_plan": days_plan,
        "hotels": hotels, "estimated_cost": sum(s["price"] for s in scenics[:req.days]),
        "ai_powered": False,
        "inferred_needs": _local_infer_needs(req),  # 降级也能展示隐式需求识别
    }


def _force_day_count(data: dict[str, Any], req: PlanRequest,
                     products: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """兜底校正：无论 AI 返回几天，都强制 days_plan 恰好为 req.days 天。"""
    plan = data.get("days_plan") or []
    if not isinstance(plan, list):
        plan = []
    target = req.days
    # 天数不足：用真实商品按天轮换补足，避免空白天
    if len(plan) < target:
        scenics = products.get("scenics") or []
        hotels = products.get("hotels") or []
        for d in range(len(plan) + 1, target + 1):
            spot = scenics[(d - 1) % len(scenics)] if scenics else None
            hotel = hotels[0] if hotels else None
            recs = ([spot] if spot else []) + ([hotel] if hotel and d == 1 else [])
            plan.append({
                "day": d,
                "title": f"第{d}天 · {'深度探索' if not spot else str(spot.get('name', '').split('-')[0])}",
                "morning": "酒店早餐后出发，开启当天旅程",
                "afternoon": f"游览 {spot['name']}" if spot else "城市深度漫步与自由探索",
                "evening": "品尝当地特色美食，感受夜色",
                "recommendations": recs,
            })
    # 天数超出：截断
    elif len(plan) > target:
        plan = plan[:target]
    # 重新编号，保证 day 字段连续 1..N
    for i, day in enumerate(plan, 1):
        if isinstance(day, dict):
            day["day"] = i
    data["days_plan"] = plan
    data["days"] = target
    return data


SYSTEM_PROMPT = """你是一位专业旅游规划师。基于我提供的真实商品数据，为用户生成一份结构与内容都合理的旅行行程。
严格输出 JSON（不要 markdown 代码块），格式如下：
{
  "summary": "一句话行程概述，60字以内",
  "days_plan": [
    {"day":1,"title":"第1天主题","morning":"上午安排","afternoon":"下午安排","evening":"晚上安排",
     "recommendations":[{"refId":商品ID,"name":"商品名","type":"hotel/scenic/transfer","price":数字}]}
  ],
  "hotels":[{"refId":商品ID,"name":"酒店名","type":"hotel","price":数字}],
  "estimated_cost": 预估总花费数字,
  "tips": "出行小贴士",
  "inferred_needs": [
    {"need":"识别出的隐性需求（如 减少步行强度）","from":"从用户哪句话/哪个词推断的（原话摘录，如 带老人）",
     "reason":"为什么这句话意味着这个需求（一句话解释，让用户能看懂你的推理）",
     "adjusted":"行程里为此做了什么具体调整（一句话，如 每天步行控制在3个景点内、安排接驳车）"}
  ],
  "references":["引用的攻略出处字符串，如 三亚线路推荐.md"]
}
规则：
0. 【实时数据优先】若我提供了【实时天气数据】，必须严格遵守其中的雨天/恶劣天气调整指令：有雨雪雾雷暴的日期把户外景点改为室内备选，并在安排或 tips 中说明；这是最高优先级的实时约束，优先于静态攻略里的户外推荐。
0b. 【隐式需求识别·重要】仔细读用户输入和同行者描述，从字面之外推断没说出口的约束，输出到 inferred_needs。常见映射：
   - "老人/长辈/父母(年纪大)" → 减少步行强度、避免爬山和长距离换乘、安排接驳车和午休、避开深夜转机；
   - "小孩/儿童/亲子/带孩子" → 安排午休与亲子友好景点、避免过晚活动、餐饮口味温和；
   - "轮椅/腿脚不便/行动不便" → 无障碍设施、少台阶、优先接驳；
   - "预算紧/省钱/学生" → 优先性价比商品、免费景点、避开高价项目；
   - "怕爬山/不想走太多/轻松点" → 平地景点、减少每日打卡数量；
   - "拍照/摄影/出片" → 观景平台和光线好的时段。
   只输出确实能从原话推断出的项（一般 1-3 条），不要凭空捏造；每条的 from 必须是用户原话里的词或短句，reason 要让普通用户一眼看懂推理逻辑。没有明显隐性需求时给空数组 []。
   识别出的需求必须真正体现在 days_plan 的安排里（adjusted 要与行程一致），不能只说不做。
1. 我会提供两类素材：【平台商品】(含可下单的 refId/价格) 和【旅游攻略知识】(景点特色、线路安排、交通美食等经验)。
2. recommendations 和 hotels 中的 refId、name、price 必须来自【平台商品】，不得编造；若某类商品为空则给空数组。
3. 每天的具体安排、游览顺序、体验亮点、注意事项，要充分吸收【旅游攻略知识】，让行程有理有据、有画面感。
4. references 填写你实际参考过的攻略出处（就是【】里"出处:"后的文件名），没有则空数组。
5. 控制总费用尽量不超过预算，结合用户偏好。
6. 只输出纯 JSON。
7. 【最重要·硬性】days_plan 必须【恰好】包含用户指定的天数 N 个对象（我会在输入中写明"行程 N 天"），
   day 编号从 1 连续排到 N，一天都不能少、也不能多。绝不允许只输出 3 天而用户要求更多天。
   即使景点素材不足，也要把每一天都排满（可安排周边小镇、深度体验、自由活动、美食探索等），
   必须保证 days_plan 数组长度 == N。"""


async def _call_qwen(messages: list[dict[str, str]], max_tokens: int = 1200, json_mode: bool = True) -> str:
    payload: dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    # 用 AsyncClient：等待大模型响应时不阻塞事件循环，多个用户请求互不排队
    async with httpx.AsyncClient(timeout=28) as client:
        resp = await client.post(
            f"{DASHSCOPE_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


@router.post("/plan")
async def ai_plan(req: PlanRequest) -> dict[str, Any]:
    products = _collect_products(req.areaId)
    # 实时天气：工具调用注入（失败静默降级，不影响主流程）
    weather = await _fetch_weather(req.city, req.days)
    if not DASHSCOPE_API_KEY:
        fb = _fallback_plan(req, products)
        fb["weather"] = weather
        return fb

    # 检索旅游攻略知识库（与城市商品数据互补）
    kb_text, kb_sources = _kb_context(
        req.city, f"{req.city} {req.preferences} {req.days}日 线路 景点 行程安排", top_k=6
    )
    context = (
        f"目的地：{req.city}（areaId={req.areaId}），行程 {req.days} 天，"
        f"预算 {req.budget:.0f} 元，用户偏好：{req.preferences or '无特殊偏好'}。\n"
        f"用户原始描述（隐式需求识别的关键依据，请逐字分析其中的隐含约束）："
        f"{req.raw_input or req.preferences or '无'}。\n\n"
        "=== 平台商品（可下单，refId 来自这里）===\n"
        "可选酒店：" + json.dumps(products["hotels"], ensure_ascii=False) + "\n"
        "可选景点：" + json.dumps(products["scenics"], ensure_ascii=False) + "\n"
        "可选接送：" + json.dumps(products["transfers"], ensure_ascii=False)
    )
    if kb_text:
        context += "\n\n=== 旅游攻略知识（行程安排与体验亮点参考）===\n" + kb_text
    wx_prompt = _weather_prompt(weather)
    if wx_prompt:
        context += "\n\n" + wx_prompt
    try:
        content = await _call_qwen(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]
        )
        data = json.loads(content)
        data["city"] = req.city
        data["days"] = req.days
        data["budget"] = req.budget
        data["ai_powered"] = True
        data["weather"] = weather
        data["weather_used"] = bool(wx_prompt)
        # 合并知识库引用来源（AI 自报 + 检索命中，去重）
        refs = list(data.get("references") or [])
        for s in kb_sources:
            label = s["source"]
            if label not in refs:
                refs.append(label)
        data["references"] = refs
        data["kb_sources"] = kb_sources
        # 隐式需求：AI 没给或格式不对时用本地关键词兜底，保证一定有可解释输出
        inferred = data.get("inferred_needs")
        if not isinstance(inferred, list) or not inferred:
            inferred = _local_infer_needs(req)
        else:
            cleaned = []
            for item in inferred:
                if isinstance(item, dict) and item.get("need"):
                    cleaned.append({
                        "need": str(item.get("need", ""))[:60],
                        "from": str(item.get("from", ""))[:60],
                        "reason": str(item.get("reason", ""))[:160],
                        "adjusted": str(item.get("adjusted", ""))[:160],
                    })
                if len(cleaned) >= 4:
                    break
            inferred = cleaned or _local_infer_needs(req)
        data["inferred_needs"] = inferred
        # 硬性校正：保证返回的天数严格等于用户请求天数
        data = _force_day_count(data, req, products)
        return data
    except Exception as exc:  # 超时 / 解析失败 / 额度问题 → 降级
        fb = _fallback_plan(req, products)
        fb["weather"] = weather
        fb["notice"] = f"AI 服务暂时不可用，已切换本地推荐（{type(exc).__name__}）"
        return fb


def _align_days_to(data: dict[str, Any], ref_plan: dict[str, Any]) -> dict[str, Any]:
    """微调后对齐天数：以原行程天数为准，缺天从原行程补回，多余截断。"""
    target = int(ref_plan.get("days") or len(ref_plan.get("days_plan") or []) or 3)
    plan = data.get("days_plan") or []
    if not isinstance(plan, list):
        plan = []
    ref_days = ref_plan.get("days_plan") or []
    if len(plan) < target:
        for d in range(len(plan) + 1, target + 1):
            src = ref_days[d - 1] if d - 1 < len(ref_days) else None
            if isinstance(src, dict):
                restored = json.loads(json.dumps(src))
                restored["day"] = d
                plan.append(restored)
            else:
                plan.append({"day": d, "title": f"第{d}天", "morning": "自由活动",
                             "afternoon": "城市漫步", "evening": "品尝当地美食",
                             "recommendations": []})
    elif len(plan) > target:
        plan = plan[:target]
    for i, day in enumerate(plan, 1):
        if isinstance(day, dict):
            day["day"] = i
    data["days_plan"] = plan
    data["days"] = target
    return data


@router.post("/chat")
async def ai_chat(req: ChatRequest) -> dict[str, Any]:
    """自由问答：回答关于该城市旅游/本平台商品的问题。"""
    if not DASHSCOPE_API_KEY:
        return {"ok": False, "answer": "未配置 AI Key，请先在 .env 配置 DASHSCOPE_API_KEY。", "ai_powered": False}
    products = _collect_products(req.areaId, limit=4)
    ctx = json.dumps(products, ensure_ascii=False)
    # 优先用前端传入的城市，否则从消息里识别
    msg_city = req.city or ""
    if not msg_city:
        for kb_city in KB.cities:
            if kb_city in req.message:
                msg_city = kb_city
                break
    kb_text, kb_sources = _kb_context(msg_city, req.message, top_k=4) if msg_city else ("", [])
    system = (
        "你是云游旅行平台的智能客服。基于提供的真实商品数据和旅游攻略知识回答用户问题，"
        "语气友好专业，回答简洁（250字内），可引用商品名、价格和攻略里的实用建议，不要编造数据。\n"
        "平台商品：" + ctx
    )
    if kb_text:
        system += "\n\n旅游攻略知识（回答的依据）：\n" + kb_text
    try:
        answer = await _call_qwen(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": req.message},
            ],
            max_tokens=500,
            json_mode=False,
        )
        return {"ok": True, "answer": answer, "ai_powered": True, "kb_sources": kb_sources}
    except Exception as exc:
        return {"ok": False, "answer": f"AI 服务暂时不可用：{exc}", "ai_powered": False}


# ==================== 多轮对话式改行程 ====================

class RefineRequest(BaseModel):
    plan: dict[str, Any] = Field(description="当前完整行程对象")
    message: str = Field(description="用户的修改指令")
    city: str = Field(default="", description="城市名")
    history: list[dict[str, str]] = Field(default_factory=list, description="对话历史")


REFINE_SYSTEM_PROMPT = """你是一位专业旅游规划师，正在帮用户调整一份已经生成好的旅行行程。
我会给你【当前行程】的 JSON 和用户的【修改要求】。你要输出【修改后的完整行程 JSON】。

输出格式与输入行程完全一致（保留 summary / days_plan / hotels / estimated_cost / tips / references 等字段），并额外增加两个字段：
{
  "changed_days": [被改动的天数编号数组，如 [2]],
  "changes": "一句话说明这次改了什么，40字以内"
}

铁律：
1. recommendations 和 hotels 中的 refId、name、price 必须原样来自【当前行程】中已出现的商品，绝对不要编造新的 refId 或商品名。用户要求"换掉某商品"时，只能在当前行程已有的商品里替换，或删掉，不能新增没出现过的商品。
2. 只改动用户明确提到的部分，其它天、其它安排、预算数字尽量保持不变。
3. days_plan 的天数、day 编号保持不变。
4. 每天仍含 day/title/morning/afternoon/evening/recommendations。
5. 只输出纯 JSON，不要 markdown 代码块。"""


def _valid_refs(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """收集当前行程里出现过的所有合法商品，用于清洗 AI 结果，防止编造。"""
    valid: dict[str, dict[str, Any]] = {}
    for x in (plan.get("hotels") or []):
        if x and x.get("refId") is not None:
            valid[str(x["refId"])] = x
    for day in (plan.get("days_plan") or []):
        for x in (day.get("recommendations") or []):
            if x and x.get("refId") is not None:
                valid[str(x["refId"])] = x
    return valid


def _clean_refs(data: dict[str, Any], valid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """剔除 AI 结果里 refId 不在合法集合中的商品，避免下单错配。"""
    def clean(lst: Any) -> list[Any]:
        out = []
        for x in (lst or []):
            if x and x.get("refId") is not None and str(x["refId"]) in valid:
                out.append(x)
        return out
    for day in (data.get("days_plan") or []):
        day["recommendations"] = clean(day.get("recommendations"))
    data["hotels"] = clean(data.get("hotels"))
    return data


def _fallback_refine(req: RefineRequest) -> dict[str, Any]:
    d = json.loads(json.dumps(req.plan or {}))
    d["changed_days"] = []
    d["changes"] = "AI 服务暂不可用，本次未做修改"
    d["ai_powered"] = False
    d["notice"] = "未配置 AI Key 或调用失败，行程保持原样"
    return d


@router.post("/refine")
async def ai_refine(req: RefineRequest) -> dict[str, Any]:
    """多轮改行程：把当前行程 + 用户指令 + 历史交给 AI，返回调整后的完整行程。"""
    if not DASHSCOPE_API_KEY:
        return _fallback_refine(req)
    plan = req.plan or {}
    valid = _valid_refs(plan)
    messages: list[dict[str, str]] = [{"role": "system", "content": REFINE_SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": "【当前行程 JSON】\n" + json.dumps(plan, ensure_ascii=False)})
    for h in (req.history or [])[-6:]:
        if h.get("content"):
            messages.append({"role": h.get("role", "user"), "content": h["content"]})
    messages.append({"role": "user", "content": req.message})
    try:
        content = await _call_qwen(messages, max_tokens=1600, json_mode=True)
        data = json.loads(content)
        data = _clean_refs(data, valid)
        data = _align_days_to(data, plan)
        data["city"] = req.city or plan.get("city", "")
        data["budget"] = plan.get("budget")
        data["ai_powered"] = True
        if not data.get("references"):
            data["references"] = plan.get("references", [])
        return data
    except Exception as exc:
        fb = _fallback_refine(req)
        fb["notice"] = f"AI 调整失败，行程保持原样（{type(exc).__name__}）"
        return fb


# ==================== AI 随行导游（景点 RAG 讲解） ====================

class GuideRequest(BaseModel):
    spot: str = Field(description="景点名")
    city: str = Field(default="", description="城市名")
    question: str = Field(default="", description="追问；为空则是首次讲解")
    history: list[dict[str, str]] = Field(default_factory=list, description="对话历史")


GUIDE_SYSTEM_PROMPT = """你是景区的 AI 随行导游"小云"，正在给站在景点前的游客做现场讲解。
你要基于我提供的【景点资料】（来自平台旅游攻略知识库）来讲，语气亲切、生动、口语化，像真人导游一样。
讲解要有画面感，包含：这个景点是什么、有什么历史或传说、最值得看的点、实用的游玩小贴士。
控制在 220 字以内。资料里没有的信息不要编造具体史实，可用"据说""流传"等说法带过。
游客追问时针对问题回答，同样基于资料。"""


@router.post("/guide")
async def ai_guide(req: GuideRequest) -> dict[str, Any]:
    """AI 随行导游：用知识库 RAG 为任意景点生成讲解，支持连续追问。"""
    if not DASHSCOPE_API_KEY:
        return {"ok": False, "answer": "未配置 AI Key，无法提供随行讲解。", "ai_powered": False}
    kb_text = ""
    sources: list[dict[str, Any]] = []
    city = _kb_city(req.city) if req.city else None
    query = f"{req.spot} {req.spot} 景点 介绍 特色"
    try:
        hits = KB.search(query, city=city, top_k=4)
        if not hits:  # 城市过滤没命中就全库按景点名找
            hits = KB.search(query, top_k=4)
        for h in hits:
            kb_text += f"【{h['contentType']}｜{h['title']}｜出处:{h['source']}】\n{h['text']}\n\n"
            sources.append({"title": h["title"], "source": h["source"], "contentType": h["contentType"]})
    except Exception:
        pass
    system = GUIDE_SYSTEM_PROMPT
    system += ("\n\n【景点资料】\n" + kb_text) if kb_text else "\n\n（知识库暂无该景点资料，请用通用常识做简短介绍，并提醒游客资料有限。）"
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for h in (req.history or [])[-6:]:
        if h.get("content"):
            messages.append({"role": h.get("role", "user"), "content": h["content"]})
    messages.append({"role": "user", "content": req.question or f"请给我讲解「{req.spot}」这个景点。"})
    try:
        answer = await _call_qwen(messages, max_tokens=500, json_mode=False)
        return {"ok": True, "answer": answer, "spot": req.spot, "ai_powered": True, "kb_sources": sources}
    except Exception as exc:
        return {"ok": False, "answer": f"讲解服务暂时不可用：{exc}", "ai_powered": False}


# ==================== 多人偏好协作规划（差异化亮点） ====================
# 痛点：家庭/朋友多人出行，各人诉求冲突（A想爬山、B想逛街、C预算紧、D带孩子），
# 现有工具多假设单一用户输入偏好。这里让每位同行者各填偏好，由 AI 生成折中方案，
# 并明确告诉每个人「保留了什么 / 让了什么 / 换来了什么」，把协作过程可视化。
# 返回结构与 /plan 完全一致，因此地图联动、下单、海报均可直接复用。

class TravelerPref(BaseModel):
    name: str = Field(default="同行者", description="同行者称呼")
    preferences: str = Field(default="", description="此人的游玩偏好或诉求")


class GroupPlanRequest(BaseModel):
    areaId: int = Field(default=2, description="目的地城市 area_id")
    city: str = Field(default="北京", description="目的地城市名")
    days: int = Field(default=3, ge=1, le=7, description="行程天数")
    budget: float = Field(default=3000, gt=0, description="总预算")
    travelers: list[TravelerPref] = Field(default_factory=list, description="同行者及各自偏好")


GROUP_SYSTEM_PROMPT = """你是一位擅长协调多人出行诉求的专业旅游规划师。多位同行者各有不同甚至冲突的偏好（如有人想爬山、有人想逛街、有人预算紧、有人带孩子），你的任务是在一份行程里尽量兼顾所有人，给出让大家都认可的折中方案。
严格输出 JSON（不要 markdown 代码块），格式如下：
{
  "summary": "一句话行程概述，60字以内，体现这是多人折中方案",
  "days_plan": [
    {"day":1,"title":"第1天主题","morning":"上午安排","afternoon":"下午安排","evening":"晚上安排",
     "recommendations":[{"refId":商品ID,"name":"商品名","type":"hotel/scenic/transfer","price":数字}]}
  ],
  "hotels":[{"refId":商品ID,"name":"酒店名","type":"hotel","price":数字}],
  "estimated_cost": 预估总花费数字,
  "tips": "给这群人的出行小贴士",
  "references":["引用的攻略出处字符串"],
  "consensus": "所有人都能共同接受的诉求，一句话，30字内",
  "compromises": [
    {"name":"同行者称呼","kept":"TA被保留满足的偏好，一句话","gaveUp":"TA做出的让步，一句话","gained":"TA换来的替代体验或补偿，一句话"}
  ],
  "balance_note": "整体如何平衡大家诉求的说明，50字内"
}
协调规则：
0. 【最重要】compromises 数组必须为每一位同行者生成一项，name 原样使用我提供的称呼，一位都不能少；每项都要真诚说明 TA 保留了什么、让步了什么、换来了什么，不要写空话。
1. 找出大家的共同点写进 consensus；对冲突的诉求用「分时段满足」「分组活动后汇合」「用相近体验替代」等手法折中，尽量让每个人的核心诉求都被照顾到。
2. 【实时数据优先】若提供了【实时天气数据】，必须遵守雨天/恶劣天气调整指令，把恶劣天气当天的户外景点换成室内备选，并在安排或 tips 中说明。
3. recommendations 和 hotels 中的 refId、name、price 必须来自【平台商品】，不得编造；某类商品为空则给空数组。
4. 每天的具体安排要吸收【旅游攻略知识】，让行程有理有据、有画面感；references 填实际参考的攻略出处。
5. 控制总费用尽量不超过预算，尤其照顾提出预算诉求的成员。
6. 只输出纯 JSON。
7. 【硬性】days_plan 必须【恰好】包含指定天数 N 个对象，day 从 1 连续排到 N，一天不能少也不能多；即使素材不足也要排满每天。"""


def _fallback_group_plan(req: GroupPlanRequest, products: dict[str, list[dict[str, Any]]],
                         weather: dict[str, Any] | None) -> dict[str, Any]:
    """多人协作降级：用基础行程 + 程序化生成的折中说明，保证演示不中断。"""
    base_req = PlanRequest(areaId=req.areaId, city=req.city, days=req.days,
                           budget=req.budget, preferences="多人折中")
    data = _fallback_plan(base_req, products)
    data["weather"] = weather
    data["consensus"] = "大家一起看风景、尝美食、留下美好回忆"
    data["compromises"] = [
        {
            "name": (t.name or f"同行者{i + 1}"),
            "kept": f"偏好「{t.preferences or '休闲观光'}」已纳入行程考虑",
            "gaveUp": "部分时段需与同行者共同安排",
            "gained": "体验了更丰富的整体行程",
        }
        for i, t in enumerate(req.travelers or [])
    ]
    data["balance_note"] = "AI 服务暂不可用，已按通用折中规则生成（本地推荐模式）"
    data["ai_powered"] = False
    return data


@router.post("/group-plan")
async def ai_group_plan(req: GroupPlanRequest) -> dict[str, Any]:
    """多人偏好协作规划：各同行者填偏好，AI 生成兼顾所有人的折中行程。"""
    travelers = req.travelers or []
    # 数据库临时不可用时降级为空商品，避免整个协作接口 500（不影响 AI 折中逻辑）
    try:
        products = _collect_products(req.areaId)
    except Exception:
        products = {"hotels": [], "scenics": [], "transfers": []}
    weather = await _fetch_weather(req.city, req.days)
    if not DASHSCOPE_API_KEY:
        return _fallback_group_plan(req, products, weather)

    pref_text = "\n".join(
        f"- {i + 1}. {t.name or ('同行者' + str(i + 1))}：{t.preferences or '无特别偏好'}"
        for i, t in enumerate(travelers)
    ) or "- 无具体同行者偏好"
    kb_text, kb_sources = _kb_context(
        req.city, f"{req.city} {' '.join(t.preferences for t in travelers)} {req.days}日 线路 景点 行程", top_k=6
    )
    context = (
        f"目的地：{req.city}（areaId={req.areaId}），行程 {req.days} 天，总预算 {req.budget:.0f} 元。\n\n"
        "=== 同行者及各自偏好 ===\n" + pref_text + "\n"
        "请为每一位同行者生成 compromises 中的一项，务必兼顾各方诉求。\n\n"
        "=== 平台商品（可下单，refId 来自这里）===\n"
        "可选酒店：" + json.dumps(products["hotels"], ensure_ascii=False) + "\n"
        "可选景点：" + json.dumps(products["scenics"], ensure_ascii=False) + "\n"
        "可选接送：" + json.dumps(products["transfers"], ensure_ascii=False)
    )
    if kb_text:
        context += "\n\n=== 旅游攻略知识（行程安排与体验亮点参考）===\n" + kb_text
    wx_prompt = _weather_prompt(weather)
    if wx_prompt:
        context += "\n\n" + wx_prompt
    try:
        content = await _call_qwen(
            [
                {"role": "system", "content": GROUP_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            max_tokens=2000,
        )
        data = json.loads(content)
        data["city"] = req.city
        data["days"] = req.days
        data["budget"] = req.budget
        data["ai_powered"] = True
        data["weather"] = weather
        data["weather_used"] = bool(wx_prompt)
        data["travelers"] = [{"name": t.name or f"同行者{i + 1}", "preferences": t.preferences}
                             for i, t in enumerate(travelers)]
        # 复用商品校验 + 天数校正，防止编造 refId 与天数错乱
        # 注意：可信源必须是数据库真实商品 products，不能用 AI 自己的返回校验自己
        real_valid: dict[str, dict[str, Any]] = {}
        for group in (products.get("hotels") or []) + (products.get("scenics") or []) + (products.get("transfers") or []):
            if group and group.get("refId") is not None:
                real_valid[str(group["refId"])] = group
        base_req = PlanRequest(areaId=req.areaId, city=req.city, days=req.days,
                               budget=req.budget, preferences="多人折中")
        data = _clean_refs(data, real_valid)
        data = _force_day_count(data, base_req, products)
        # 合并知识库引用来源
        refs = list(data.get("references") or [])
        for s in kb_sources:
            if s["source"] not in refs:
                refs.append(s["source"])
        data["references"] = refs
        data["kb_sources"] = kb_sources
        return data
    except Exception as exc:
        fb = _fallback_group_plan(req, products, weather)
        fb["notice"] = f"AI 服务暂时不可用，已切换本地折中推荐（{type(exc).__name__}）"
        return fb
