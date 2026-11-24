"""运营管理后台：给工作人员查看平台数据的只读统计接口。

与 C 端（游客）接口区分：统一前缀 /api/admin/*，只读为主，登录为演示级鉴权。
所有统计基于真实业务表；单表查询失败时降级为 0/空，不影响整体返回。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..database import fetch_all, fetch_one
from ..knowledge import KB

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 演示级账号（真实项目应接入服务端 session / JWT + 数据库用户表）
ADMIN_USER = "admin"
ADMIN_PASSWORD = "123456"
DEMO_TOKEN = "yunyou-admin-demo-token"

# 枚举中文映射
ORDER_TYPE_LABEL = {
    "hotel_room": "酒店",
    "scenic_ticket": "景点门票",
    "flight_cabin": "机票",
    "train_seat": "火车票",
    "bus_seat": "汽车票",
    "transfer_service": "接送服务",
}
ORDER_STATUS_LABEL = {
    "pending_payment": "待支付",
    "paid": "已支付",
    "in_progress": "进行中",
    "finished": "已完成",
    "cancelled": "已取消",
}
MEMBER_LEVEL_LABEL = {"normal": "普通会员", "silver": "银卡会员", "gold": "金卡会员"}
REFUND_STATUS_LABEL = {
    "pending": "待处理",
    "approved": "已通过",
    "rejected": "已驳回",
    "success": "已退款",
}
GUIDE_LEVEL_LABEL = {"gold": "金牌导游", "senior": "高级导游", "standard": "标准导游"}
BOOKING_STATUS_LABEL = {
    "pending": "待确认",
    "confirmed": "已确认",
    "completed": "已完成",
    "cancelled": "已取消",
}


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


def _safe_all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    try:
        return fetch_all(sql, params)
    except Exception:
        return []


def _safe_one(sql: str, params: Any = None) -> dict[str, Any] | None:
    try:
        return fetch_one(sql, params)
    except Exception:
        return None


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


@router.post("/login")
def login(body: LoginBody) -> dict[str, Any]:
    """演示级登录：账号 admin / 密码 123456。"""
    if body.username == ADMIN_USER and body.password == ADMIN_PASSWORD:
        return {"ok": True, "token": DEMO_TOKEN, "username": ADMIN_USER}
    return {"ok": False, "message": "账号或密码错误"}


@router.get("/overview")
def overview() -> dict[str, Any]:
    """核心指标卡：订单、营收、用户、会员、退款、导游等。"""
    order = _safe_one("SELECT COUNT(*) c, COALESCE(SUM(paid_amount),0) revenue FROM orders") or {}
    user = _safe_one("SELECT COUNT(*) c FROM users") or {}
    member = _safe_one("SELECT COUNT(*) c FROM member_accounts") or {}
    pay = _safe_one("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE status_code='success'") or {}
    refund = _safe_one("SELECT COUNT(*) c, COALESCE(SUM(requested_amount),0) amt FROM refund_requests WHERE status_code='pending'") or {}
    guide = _safe_one("SELECT COUNT(*) c FROM guides WHERE status_code='active'") or {}
    booking = _safe_one("SELECT COUNT(*) c FROM guide_bookings") or {}
    today = _safe_one(
        "SELECT COUNT(*) c, COALESCE(SUM(paid_amount),0) amt FROM orders WHERE DATE(created_at)=CURDATE()"
    ) or {}
    return {
        "cards": [
            {"key": "orders", "label": "累计订单", "value": int(order.get("c") or 0), "unit": "单", "icon": "📋", "tone": "blue"},
            {"key": "revenue", "label": "累计营收", "value": round(_num(order.get("revenue")), 2), "unit": "元", "icon": "💰", "tone": "green"},
            {"key": "users", "label": "注册用户", "value": int(user.get("c") or 0), "unit": "人", "icon": "👥", "tone": "purple"},
            {"key": "members", "label": "会员账户", "value": int(member.get("c") or 0), "unit": "个", "icon": "💎", "tone": "orange"},
            {"key": "payments", "label": "支付成功额", "value": round(_num(pay.get("s")), 2), "unit": "元", "icon": "✅", "tone": "teal"},
            {"key": "refundPending", "label": "待处理退款", "value": int(refund.get("c") or 0), "unit": "笔", "icon": "↩️", "tone": "red"},
            {"key": "guides", "label": "在岗导游", "value": int(guide.get("c") or 0), "unit": "位", "icon": "🧭", "tone": "indigo"},
            {"key": "bookings", "label": "导游预约", "value": int(booking.get("c") or 0), "unit": "单", "icon": "📅", "tone": "pink"},
        ],
        "today": {
            "orders": int(today.get("c") or 0),
            "amount": round(_num(today.get("amt")), 2),
        },
    }


@router.get("/order-trend")
def order_trend(days: int = Query(default=14, ge=7, le=90)) -> dict[str, Any]:
    """最近 N 天订单量与营收趋势。"""
    start = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = _safe_all(
        """SELECT DATE(created_at) d, COUNT(*) cnt, COALESCE(SUM(paid_amount),0) amt
           FROM orders WHERE DATE(created_at)>=%s GROUP BY DATE(created_at) ORDER BY d""",
        (start,),
    )
    by_day = {str(r["d"]): r for r in rows}
    labels, counts, amounts = [], [], []
    for i in range(days):
        d = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        labels.append(d[5:])
        r = by_day.get(d)
        counts.append(int(r["cnt"]) if r else 0)
        amounts.append(round(_num(r["amt"]), 2) if r else 0.0)
    return {"labels": labels, "counts": counts, "amounts": amounts}


@router.get("/order-distribution")
def order_distribution() -> dict[str, Any]:
    """订单类型分布 + 状态分布。"""
    type_rows = _safe_all("SELECT order_type_code k, COUNT(*) c FROM orders GROUP BY order_type_code")
    status_rows = _safe_all("SELECT status_code k, COUNT(*) c FROM orders GROUP BY status_code")
    types = [
        {"name": ORDER_TYPE_LABEL.get(r["k"], str(r["k"])), "value": int(r["c"])}
        for r in type_rows
    ]
    statuses = [
        {"name": ORDER_STATUS_LABEL.get(r["k"], str(r["k"])), "value": int(r["c"])}
        for r in status_rows
    ]
    return {"types": types, "statuses": statuses}


@router.get("/orders")
def orders_list(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """订单明细列表（含用户名）。"""
    rows = _safe_all(
        """SELECT o.order_no, u.nickname, o.order_type_code, o.status_code,
                  o.payable_amount, o.paid_amount, o.created_at
           FROM orders o LEFT JOIN users u ON u.id=o.user_id
           ORDER BY o.created_at DESC LIMIT %s""",
        (limit,),
    )
    items = [
        {
            "orderNo": r["order_no"],
            "user": r.get("nickname") or "-",
            "type": ORDER_TYPE_LABEL.get(r["order_type_code"], r["order_type_code"]),
            "status": ORDER_STATUS_LABEL.get(r["status_code"], r["status_code"]),
            "payable": round(_num(r["payable_amount"]), 2),
            "paid": round(_num(r["paid_amount"]), 2),
            "createdAt": str(r["created_at"])[:19] if r.get("created_at") else "",
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/users")
def users_list(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """用户与会员等级列表。"""
    rows = _safe_all(
        """SELECT u.id, u.nickname, u.phone, u.status_code, u.created_at,
                  m.member_level_code, m.points_balance
           FROM users u LEFT JOIN member_accounts m ON m.user_id=u.id
           ORDER BY u.created_at DESC LIMIT %s""",
        (limit,),
    )
    items = [
        {
            "id": r["id"],
            "nickname": r.get("nickname") or "-",
            "phone": r.get("phone") or "",
            "status": r.get("status_code") or "normal",
            "member": MEMBER_LEVEL_LABEL.get(r.get("member_level_code"), "—"),
            "points": int(r.get("points_balance") or 0),
            "createdAt": str(r["created_at"])[:10] if r.get("created_at") else "",
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/refunds")
def refunds_list(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """退款申请列表。"""
    rows = _safe_all(
        """SELECT rr.refund_request_no, rr.status_code, rr.requested_amount,
                  rr.approved_amount, rr.requested_at
           FROM refund_requests rr ORDER BY rr.requested_at DESC LIMIT %s""",
        (limit,),
    )
    items = [
        {
            "no": r["refund_request_no"],
            "status": REFUND_STATUS_LABEL.get(r["status_code"], r["status_code"]),
            "requested": round(_num(r["requested_amount"]), 2),
            "approved": round(_num(r["approved_amount"]), 2),
            "requestedAt": str(r["requested_at"])[:19] if r.get("requested_at") else "",
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/guides")
def guides_list(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """导游档案 + 预约记录。"""
    rows = _safe_all(
        """SELECT guide_name, city_name, level_code, rating, service_count,
                  price_per_day, status_code FROM guides ORDER BY rating DESC LIMIT %s""",
        (limit,),
    )
    guides = [
        {
            "name": r["guide_name"],
            "city": r.get("city_name") or "-",
            "level": GUIDE_LEVEL_LABEL.get(r["level_code"], r["level_code"]),
            "rating": round(_num(r["rating"]), 2),
            "services": int(r.get("service_count") or 0),
            "price": round(_num(r["price_per_day"]), 2),
            "status": r.get("status_code") or "active",
        }
        for r in rows
    ]
    bookings = _safe_all(
        """SELECT booking_no, city_name, service_date, days, total_amount, status_code
           FROM guide_bookings ORDER BY created_at DESC LIMIT 20"""
    )
    booking_items = [
        {
            "no": b["booking_no"],
            "city": b.get("city_name") or "-",
            "date": str(b.get("service_date") or ""),
            "days": int(b.get("days") or 0),
            "amount": round(_num(b.get("total_amount")), 2),
            "status": BOOKING_STATUS_LABEL.get(b["status_code"], b["status_code"]),
        }
        for b in bookings
    ]
    return {"guides": guides, "bookings": booking_items}


@router.get("/ai-ops")
def ai_ops() -> dict[str, Any]:
    """AI 运营数据：知识库覆盖 + 商品/攻略规模 + 预订联动。"""
    kb = KB.stats()
    city_count = _safe_one("SELECT COUNT(*) c FROM areas WHERE level=2 AND status_code='active'") or {}
    product_counts = {
        "hotels": int((_safe_one("SELECT COUNT(*) c FROM hotels") or {}).get("c") or 0),
        "scenic": int((_safe_one("SELECT COUNT(*) c FROM scenic_spots") or {}).get("c") or 0),
        "flights": int((_safe_one("SELECT COUNT(*) c FROM flight_departures") or {}).get("c") or 0),
        "trains": int((_safe_one("SELECT COUNT(*) c FROM train_departures") or {}).get("c") or 0),
    }
    # AI 行程与导游预约的联动量：plan_summary 非空即视为 AI 行程带来的预约
    linked = _safe_one(
        "SELECT COUNT(*) c FROM guide_bookings WHERE plan_summary IS NOT NULL AND plan_summary<>''"
    ) or {}
    return {
        "kb": {
            "chunks": int(kb.get("chunks") or 0),
            "cities": kb.get("cities") or [],
            "byType": kb.get("byType") or {},
        },
        "areas": int(city_count.get("c") or 0),
        "products": product_counts,
        "aiLinkedBookings": int(linked.get("c") or 0),
        "model": "通义千问 qwen-flash",
        "capabilities": [
            "一句话智能规划",
            "实时天气感知与重排",
            "多轮对话微调",
            "多人偏好折中",
            "真实商品匹配与下单",
            "中转联程航班",
        ],
    }
