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


class PlanRequest(BaseModel):
    areaId: int = Field(default=2, description="目的地城市 area_id（level=2）")
    city: str = Field(default="北京", description="目的地城市名，仅用于提示词")
    days: int = Field(default=3, ge=1, le=7, description="行程天数")
    budget: float = Field(default=2000, gt=0, description="总预算（元）")
    preferences: str = Field(default="", description="偏好，如'历史文化、美食'")


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
    }


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
  "references":["引用的攻略出处字符串，如 三亚线路推荐.md"]
}
规则：
1. 我会提供两类素材：【平台商品】(含可下单的 refId/价格) 和【旅游攻略知识】(景点特色、线路安排、交通美食等经验)。
2. recommendations 和 hotels 中的 refId、name、price 必须来自【平台商品】，不得编造；若某类商品为空则给空数组。
3. 每天的具体安排、游览顺序、体验亮点、注意事项，要充分吸收【旅游攻略知识】，让行程有理有据、有画面感。
4. references 填写你实际参考过的攻略出处（就是【】里"出处:"后的文件名），没有则空数组。
5. 控制总费用尽量不超过预算，结合用户偏好。
6. 只输出纯 JSON。"""


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
    if not DASHSCOPE_API_KEY:
        return _fallback_plan(req, products)

    # 检索旅游攻略知识库（与城市商品数据互补）
    kb_text, kb_sources = _kb_context(
        req.city, f"{req.city} {req.preferences} {req.days}日 线路 景点 行程安排", top_k=6
    )
    context = (
        f"目的地：{req.city}（areaId={req.areaId}），行程 {req.days} 天，"
        f"预算 {req.budget:.0f} 元，用户偏好：{req.preferences or '无特殊偏好'}。\n\n"
        "=== 平台商品（可下单，refId 来自这里）===\n"
        "可选酒店：" + json.dumps(products["hotels"], ensure_ascii=False) + "\n"
        "可选景点：" + json.dumps(products["scenics"], ensure_ascii=False) + "\n"
        "可选接送：" + json.dumps(products["transfers"], ensure_ascii=False)
    )
    if kb_text:
        context += "\n\n=== 旅游攻略知识（行程安排与体验亮点参考）===\n" + kb_text
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
        # 合并知识库引用来源（AI 自报 + 检索命中，去重）
        refs = list(data.get("references") or [])
        for s in kb_sources:
            label = s["source"]
            if label not in refs:
                refs.append(label)
        data["references"] = refs
        data["kb_sources"] = kb_sources
        return data
    except Exception as exc:  # 超时 / 解析失败 / 额度问题 → 降级
        fb = _fallback_plan(req, products)
        fb["notice"] = f"AI 服务暂时不可用，已切换本地推荐（{type(exc).__name__}）"
        return fb


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
        data["city"] = req.city or plan.get("city", "")
        data["days"] = plan.get("days", len(data.get("days_plan") or []))
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
