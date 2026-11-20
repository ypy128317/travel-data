"""真人导游对接：导游档案查询、AI 匹配、预约下单、预约管理。"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from ..database import db_cursor, fetch_all, fetch_one
from ..dependencies import get_current_user_id
from ..errors import bad_request, conflict, not_found
from ..utils import format_date, format_datetime, local_now, make_no, money, parse_date

router = APIRouter(prefix="/api/v1", tags=["guides"])

LEVEL_LABEL = {"gold": "金牌导游", "senior": "高级导游", "standard": "标准导游"}
BOOKING_STATUS_LABEL = {
    "pending": "待导游确认",
    "confirmed": "已确认",
    "completed": "已完成",
    "cancelled": "已取消",
}
GUIDE_STATUS_LABEL = {"active": "可预约", "busy": "繁忙", "inactive": "已停用"}


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


class BookingCreateRequest(BaseModel):
    guideId: int = Field(description="导游 ID，对应 guides.id")
    serviceDate: str = Field(description="服务日期，格式 YYYY-MM-DD")
    days: int = Field(default=1, ge=1, le=15, description="服务天数")
    peopleCount: int = Field(default=1, ge=1, le=30, description="出行人数")
    contactName: str | None = Field(default=None, description="联系人姓名")
    contactPhone: str | None = Field(default=None, description="联系电话")
    remark: str | None = Field(default=None, description="游玩需求备注")
    planSummary: str | None = Field(default=None, description="关联的 AI 行程摘要（联动）")


class BookingCancelRequest(BaseModel):
    reason: str = Field(default="游客主动取消", description="取消原因")


class BookingConfirmRequest(BaseModel):
    reply: str = Field(default="已确认，期待为您服务！", description="导游确认回复")


def _guide_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "guideId": row["id"],
        "guideNo": row["guide_no"],
        "guideName": row["guide_name"],
        "areaId": row["area_id"],
        "cityName": row["city_name"],
        "levelCode": row["level_code"],
        "levelLabel": LEVEL_LABEL.get(row["level_code"], row["level_code"]),
        "rating": float(row["rating"]),
        "reviewCount": int(row["review_count"]),
        "serviceCount": int(row["service_count"]),
        "yearsExperience": int(row["years_experience"]),
        "pricePerDay": money(row["price_per_day"]),
        "languages": row["languages"],
        "specialties": row["specialties"],
        "intro": row["intro"],
        "avatarUrl": row["avatar_url"],
        "tags": _loads(row["tags_payload"], []),
        "statusCode": row["status_code"],
        "statusLabel": GUIDE_STATUS_LABEL.get(row["status_code"], row["status_code"]),
    }


def _booking_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "bookingId": row["id"],
        "bookingNo": row["booking_no"],
        "userId": row["user_id"],
        "guideId": row["guide_id"],
        "guideName": row["guide_name"],
        "guideLevel": LEVEL_LABEL.get(row["level_code"], row["level_code"]),
        "guideAvatar": row["avatar_url"],
        "areaId": row["area_id"],
        "cityName": row["city_name"],
        "serviceDate": str(row["service_date"]),
        "days": int(row["days"]),
        "peopleCount": int(row["people_count"]),
        "totalAmount": money(row["total_amount"]),
        "statusCode": row["status_code"],
        "statusLabel": BOOKING_STATUS_LABEL.get(row["status_code"], row["status_code"]),
        "contactName": row["contact_name"],
        "contactPhone": row["contact_phone"],
        "remark": row["remark"],
        "planSummary": row["plan_summary"],
        "guideReply": row["guide_reply"],
        "createdAt": format_datetime(row["created_at"]),
    }


_BOOKING_SELECT = """
    SELECT b.*, g.guide_name, g.level_code, g.avatar_url
    FROM guide_bookings b
    JOIN guides g ON g.id = b.guide_id
"""


@router.get("/guides")
def list_guides(
    area_id: Annotated[int | None, Query(alias="areaId", description="服务城市 area_id")] = None,
    city_name: Annotated[str | None, Query(alias="cityName", description="城市名模糊匹配")] = None,
    level_code: Annotated[str | None, Query(alias="levelCode", description="等级筛选：gold/senior/standard")] = None,
    keyword: Annotated[str | None, Query(description="姓名/擅长领域关键字")] = None,
    status_code: Annotated[str, Query(alias="statusCode", description="状态筛选，默认 active")] = "active",
):
    """导游列表：按城市/等级/关键字筛选。"""
    conditions = ["g.status_code = %s"]
    params: list[Any] = [status_code]
    if area_id:
        conditions.append("g.area_id = %s")
        params.append(area_id)
    if city_name:
        conditions.append("g.city_name LIKE %s")
        params.append(f"%{city_name}%")
    if level_code:
        conditions.append("g.level_code = %s")
        params.append(level_code)
    if keyword:
        conditions.append(
            "(g.guide_name LIKE %s OR g.specialties LIKE %s OR g.intro LIKE %s)"
        )
        params.extend([f"%{keyword}%"] * 3)
    where = " AND ".join(conditions)
    rows = fetch_all(
        f"""
        SELECT g.* FROM guides g
        WHERE {where}
        ORDER BY FIELD(g.level_code,'gold','senior','standard'), g.rating DESC, g.service_count DESC
        LIMIT 60
        """,
        tuple(params),
    )
    return {"list": [_guide_payload(r) for r in rows], "total": len(rows)}


@router.get("/guides/{guideId}")
def get_guide_detail(
    guide_id: Annotated[int, Path(alias="guideId", description="导游 ID")],
):
    row = fetch_one("SELECT * FROM guides WHERE id = %s", (guide_id,))
    if row is None:
        raise not_found("导游不存在")
    return _guide_payload(row)


@router.post("/guides/match")
def match_guides(body: dict[str, Any]):
    """按城市 + 偏好关键词匹配导游（供 AI 行程一键匹配）。返回带匹配分的列表。"""
    city = str(body.get("city") or "").replace("市", "").replace("省", "")
    pref = str(body.get("preferences") or "")
    area_id = body.get("areaId")
    conditions = ["status_code = 'active'"]
    params: list[Any] = []
    if area_id:
        conditions.append("area_id = %s")
        params.append(area_id)
    elif city:
        conditions.append("city_name LIKE %s")
        params.append(f"%{city}%")
    rows = fetch_all(
        f"SELECT * FROM guides WHERE {' AND '.join(conditions)} "
        "ORDER BY FIELD(level_code,'gold','senior','standard'), rating DESC LIMIT 20",
        tuple(params),
    )
    # 偏好关键词命中加分：让"亲子/美食/摄影/文化"等偏好影响排序
    kw = [w for w in pref.replace("，", ",").replace("、", ",").split(",") if w.strip()]
    scored = []
    for r in rows:
        text = (r["specialties"] or "") + (r["intro"] or "") + (r["tags_payload"] or "")
        hits = sum(1 for w in kw if w.strip() in text)
        level_bonus = {"gold": 2, "senior": 1, "standard": 0}.get(r["level_code"], 0)
        score = round(float(r["rating"]) + hits * 0.6 + level_bonus * 0.3, 2)
        item = _guide_payload(r)
        item["matchScore"] = score
        item["matchReasons"] = ([f"擅长{w.strip()}" for w in kw if w.strip() in text] or ["评分高", "经验足"])[:3]
        scored.append(item)
    scored.sort(key=lambda x: x["matchScore"], reverse=True)
    return {"list": scored[:3], "city": city, "total": len(scored)}


@router.post("/guide-bookings")
def create_booking(
    body: BookingCreateRequest,
    current_user_id: Annotated[int, Depends(get_current_user_id)],
):
    """创建导游预约单：金额 = 导游日单价 × 天数，状态 pending。"""
    service_date = parse_date(body.serviceDate, "serviceDate")
    guide = fetch_one(
        "SELECT * FROM guides WHERE id = %s AND status_code = 'active'",
        (body.guideId,),
    )
    if guide is None:
        raise not_found("导游不存在或当前不可预约")
    total = (Decimal(str(guide["price_per_day"])) * body.days).quantize(Decimal("0.01"))
    now = local_now()
    with db_cursor() as (_, cursor):
        booking_no = make_no("GB")
        cursor.execute(
            """
            INSERT INTO guide_bookings (
                booking_no, user_id, guide_id, area_id, city_name, service_date, days,
                people_count, total_amount, status_code, contact_name, contact_phone,
                remark, plan_summary, guide_reply, confirmed_at, cancelled_at,
                created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,NULL,NULL,NULL,%s,%s)
            """,
            (
                booking_no,
                current_user_id,
                guide["id"],
                guide["area_id"],
                guide["city_name"],
                service_date,
                body.days,
                body.peopleCount,
                total,
                body.contactName,
                body.contactPhone,
                body.remark,
                body.planSummary,
                now,
                now,
            ),
        )
        booking_id = cursor.lastrowid
    row = fetch_one(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
    if row is None:
        raise conflict("预约单创建失败")
    return _booking_payload(row)


@router.get("/guide-bookings")
def list_bookings(
    current_user_id: Annotated[int, Depends(get_current_user_id)],
    status_code: Annotated[str | None, Query(alias="statusCode", description="状态筛选")] = None,
):
    """当前用户的导游预约列表。"""
    conditions = ["b.user_id = %s"]
    params: list[Any] = [current_user_id]
    if status_code:
        conditions.append("b.status_code = %s")
        params.append(status_code)
    rows = fetch_all(
        _BOOKING_SELECT
        + f" WHERE {' AND '.join(conditions)} ORDER BY b.service_date DESC, b.id DESC LIMIT 100",
        tuple(params),
    )
    return {"list": [_booking_payload(r) for r in rows], "total": len(rows)}


@router.get("/guide-bookings/{bookingId}")
def get_booking_detail(
    booking_id: Annotated[int, Path(alias="bookingId", description="预约单 ID")],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
):
    row = fetch_one(
        _BOOKING_SELECT + " WHERE b.id = %s AND b.user_id = %s",
        (booking_id, current_user_id),
    )
    if row is None:
        raise not_found("预约单不存在")
    return _booking_payload(row)


@router.post("/guide-bookings/{bookingId}/cancel")
def cancel_booking(
    body: BookingCancelRequest,
    booking_id: Annotated[int, Path(alias="bookingId", description="预约单 ID")],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
):
    now = local_now()
    with db_cursor() as (_, cursor):
        cursor.execute(
            """
            SELECT id, status_code FROM guide_bookings
            WHERE id = %s AND user_id = %s FOR UPDATE
            """,
            (booking_id, current_user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise not_found("预约单不存在")
        if row["status_code"] in {"cancelled", "completed"}:
            raise conflict("当前预约状态不可取消")
        cursor.execute(
            """
            UPDATE guide_bookings
            SET status_code = 'cancelled', cancelled_at = %s, guide_reply = %s, updated_at = %s
            WHERE id = %s
            """,
            (now, f"游客取消：{body.reason}", now, booking_id),
        )
    row = fetch_one(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
    return _booking_payload(row)


@router.post("/guide-bookings/{bookingId}/confirm")
def confirm_booking(
    body: BookingConfirmRequest,
    booking_id: Annotated[int, Path(alias="bookingId", description="预约单 ID")],
    current_user_id: Annotated[int, Depends(get_current_user_id)],
):
    """演示导游接单确认（答辩可模拟导游侧操作）。"""
    now = local_now()
    with db_cursor() as (_, cursor):
        cursor.execute(
            "SELECT id, status_code FROM guide_bookings WHERE id = %s FOR UPDATE",
            (booking_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise not_found("预约单不存在")
        if row["status_code"] != "pending":
            raise conflict("只有待确认的预约可以接单")
        cursor.execute(
            """
            UPDATE guide_bookings
            SET status_code = 'confirmed', guide_reply = %s, confirmed_at = %s, updated_at = %s
            WHERE id = %s
            """,
            (body.reply, now, now, booking_id),
        )
    row = fetch_one(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
    return _booking_payload(row)
