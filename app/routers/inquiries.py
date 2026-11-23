"""C 端 AI 行程的「找顾问」线索。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..database import db_cursor, execute, fetch_all, fetch_one
from ..dependencies import get_current_user_id
from ..errors import bad_request, not_found
from ..utils import format_datetime, local_now

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])

STATUS_LABELS = {"new": "待联系", "contacted": "已联系"}


def ensure_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS quote_inquiries (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            user_id INT NOT NULL DEFAULT 1,
            customer_name VARCHAR(80) NOT NULL,
            customer_phone VARCHAR(40) NOT NULL DEFAULT '',
            city VARCHAR(80) NOT NULL,
            days INT NOT NULL,
            budget DECIMAL(12, 2) NOT NULL DEFAULT 0,
            preferences VARCHAR(500) NOT NULL DEFAULT '',
            plan_json LONGTEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'new',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            KEY idx_inquiries_user (user_id),
            KEY idx_inquiries_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


class InquiryCreate(BaseModel):
    customerName: str = Field(min_length=1, max_length=80)
    customerPhone: str = Field(default="", max_length=40)
    city: str = Field(min_length=1, max_length=80)
    days: int = Field(ge=1, le=15)
    budget: float = Field(default=0, ge=0)
    preferences: str = Field(default="", max_length=500)
    plan: dict[str, Any]
    userId: int = Field(default=1, ge=1)


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    plan = row.get("plan_json")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:
            plan = {}
    return {
        "id": row["id"],
        "customerName": row["customer_name"],
        "customerPhone": row["customer_phone"],
        "city": row["city"],
        "days": row["days"],
        "budget": float(row["budget"] or 0),
        "preferences": row["preferences"],
        "status": row["status"],
        "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "createdAt": format_datetime(row["created_at"]),
        "plan": plan,
    }


@router.post("")
def create_inquiry(payload: InquiryCreate) -> dict[str, Any]:
    now = local_now()
    with db_cursor() as (_, cursor):
        cursor.execute(
            """
            INSERT INTO quote_inquiries
            (user_id, customer_name, customer_phone, city, days, budget, preferences,
             plan_json, status, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'new',%s,%s)
            """,
            (
                payload.userId, payload.customerName.strip(), payload.customerPhone.strip(),
                payload.city, payload.days, payload.budget, payload.preferences.strip(),
                json.dumps(payload.plan, ensure_ascii=False), now, now,
            ),
        )
        inquiry_id = cursor.lastrowid
    row = fetch_one("SELECT * FROM quote_inquiries WHERE id=%s", (inquiry_id,))
    if row is None:
        raise bad_request("提交失败")
    return _payload(row)


@router.get("")
def list_inquiries(user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    rows = fetch_all(
        "SELECT * FROM quote_inquiries WHERE user_id=%s ORDER BY id DESC LIMIT 30",
        (user_id,),
    )
    return {"items": [_payload(r) for r in rows]}


@router.post("/{inquiry_id}/claim")
def claim_inquiry(inquiry_id: int, user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM quote_inquiries WHERE id=%s AND user_id=%s",
                    (inquiry_id, user_id))
    if row is None:
        raise not_found("询价线索不存在")
    execute("UPDATE quote_inquiries SET status='contacted', updated_at=%s WHERE id=%s",
            (local_now(), inquiry_id))
    return _payload(fetch_one("SELECT * FROM quote_inquiries WHERE id=%s", (inquiry_id,)))
