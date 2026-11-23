"""销售工作台报价单：AI 行程 → 销售改价 → 客户链接确认。"""

from __future__ import annotations

import json
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..config import DEMO_PAYMENT_SIGNATURE
from ..database import db_cursor, execute, fetch_all, fetch_one
from ..dependencies import get_current_user_id
from ..errors import bad_request, conflict, not_found
from ..utils import format_datetime, local_now, make_no, money
from .orders import (OrderPayRequest, PaymentCallbackRequest, handle_payment_callback,
                     pay_order)

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

STATUS_LABELS = {
    "sent": "待客户确认",
    "confirmed": "客户已确认",
    "expired": "已过期",
}


def ensure_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS quotes (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            quote_no VARCHAR(40) NOT NULL,
            token VARCHAR(80) NOT NULL,
            user_id INT NOT NULL,
            customer_name VARCHAR(80) NOT NULL,
            customer_phone VARCHAR(40) NOT NULL DEFAULT '',
            city VARCHAR(80) NOT NULL,
            days INT NOT NULL,
            budget DECIMAL(12, 2) NOT NULL DEFAULT 0,
            base_cost DECIMAL(12, 2) NOT NULL DEFAULT 0,
            markup_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
            final_price DECIMAL(12, 2) NOT NULL DEFAULT 0,
            valid_hours INT NOT NULL DEFAULT 48,
            status VARCHAR(20) NOT NULL DEFAULT 'sent',
            plan_json LONGTEXT NOT NULL,
            notes VARCHAR(500) NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            confirmed_at DATETIME NULL,
            order_id BIGINT UNSIGNED NULL,
            order_no VARCHAR(50) NULL,
            paid_at DATETIME NULL,
            payment_no VARCHAR(50) NULL,
            supplier_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            supplier_confirmed_at DATETIME NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uk_quotes_no (quote_no),
            UNIQUE KEY uk_quotes_token (token),
            KEY idx_quotes_user (user_id),
            KEY idx_quotes_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    columns = {str(row["Field"]) for row in fetch_all("SHOW COLUMNS FROM quotes")}
    for column, ddl in (
        ("order_id", "ALTER TABLE quotes ADD COLUMN order_id BIGINT UNSIGNED NULL"),
        ("order_no", "ALTER TABLE quotes ADD COLUMN order_no VARCHAR(50) NULL"),
        ("paid_at", "ALTER TABLE quotes ADD COLUMN paid_at DATETIME NULL"),
        ("payment_no", "ALTER TABLE quotes ADD COLUMN payment_no VARCHAR(50) NULL"),
        ("supplier_status", "ALTER TABLE quotes ADD COLUMN supplier_status VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("supplier_confirmed_at", "ALTER TABLE quotes ADD COLUMN supplier_confirmed_at DATETIME NULL"),
    ):
        if column not in columns:
            execute(ddl)
    execute(
        """
        CREATE TABLE IF NOT EXISTS quote_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
            quote_id BIGINT UNSIGNED NOT NULL,
            event_type VARCHAR(40) NOT NULL,
            actor VARCHAR(30) NOT NULL,
            message VARCHAR(500) NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id),
            KEY idx_quote_events_quote (quote_id),
            CONSTRAINT fk_quote_events_quote FOREIGN KEY (quote_id) REFERENCES quotes (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


class QuoteCreate(BaseModel):
    customerName: str = Field(min_length=1, max_length=80)
    customerPhone: str = Field(default="", max_length=40)
    city: str = Field(min_length=1, max_length=80)
    days: int = Field(ge=1, le=15)
    budget: float = Field(default=0, ge=0)
    baseCost: float = Field(default=0, ge=0)
    markupAmount: float = Field(default=0, ge=-1000000, le=1000000)
    validHours: int = Field(default=48, ge=1, le=168)
    plan: dict[str, Any]
    notes: str = Field(default="", max_length=500)


class QuotePayRequest(BaseModel):
    paymentMethodCode: str = Field(default="alipay", pattern="^(alipay|wechat|unionpay)$")


class QuotePriceUpdate(BaseModel):
    baseCost: float = Field(ge=0)
    markupAmount: float = Field(ge=-1000000, le=1000000)
    validHours: int = Field(default=48, ge=1, le=168)
    notes: str = Field(default="", max_length=500)


def _dec(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _expire_if_needed(row: dict[str, Any]) -> dict[str, Any]:
    if row["status"] == "sent" and row["expires_at"] < local_now():
        execute("UPDATE quotes SET status='expired' WHERE id=%s", (row["id"],))
        row["status"] = "expired"
    return row


def _payload(row: dict[str, Any], public: bool = False) -> dict[str, Any]:
    row = _expire_if_needed(row)
    plan = row.get("plan_json")
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except Exception:
            plan = {}
    data: dict[str, Any] = {
        "quoteNo": row["quote_no"],
        "token": row["token"],
        "quoteUrl": f"/quote/{row['token']}",
        "customerName": row["customer_name"],
        "city": row["city"],
        "days": row["days"],
        "budget": money(row["budget"]),
        "baseCost": money(row["base_cost"]),
        "markupAmount": money(row["markup_amount"]),
        "finalPrice": money(row["final_price"]),
        "validHours": row["valid_hours"],
        "status": row["status"],
        "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "createdAt": format_datetime(row["created_at"]),
        "expiresAt": format_datetime(row["expires_at"]),
        "confirmedAt": format_datetime(row["confirmed_at"]),
        "orderId": row.get("order_id"),
        "orderNo": row.get("order_no"),
        "orderStatus": "pending_payment" if row.get("order_id") and not row.get("paid_at") else ("paid" if row.get("paid_at") else None),
        "priceLocked": bool(row.get("order_id")),
        "paidAt": format_datetime(row.get("paid_at")),
        "paymentNo": row.get("payment_no"),
        "paymentStatus": "paid" if row.get("paid_at") else ("pending" if row.get("order_id") else None),
        "supplierStatus": row.get("supplier_status") or "pending",
        "supplierConfirmedAt": format_datetime(row.get("supplier_confirmed_at")),
        "plan": plan,
        "notes": row.get("notes") or "",
    }
    events = fetch_all(
        "SELECT event_type, actor, message, created_at FROM quote_events WHERE quote_id=%s ORDER BY id",
        (row["id"],),
    )
    data["events"] = [
        {"type": e["event_type"], "actor": e["actor"], "message": e["message"],
         "createdAt": format_datetime(e["created_at"])}
        for e in events
    ]
    if not public:
        data["customerPhone"] = row["customer_phone"]
        data["userId"] = row["user_id"]
    return data


@router.post("")
def create_quote(payload: QuoteCreate, user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    base = _dec(payload.baseCost or payload.plan.get("estimated_cost") or 0)
    markup = _dec(payload.markupAmount)
    final = _dec(base + markup)
    if final < 0:
        raise bad_request("报价不能为负数")
    now = local_now()
    expires = now + timedelta(hours=payload.validHours)
    token = secrets.token_urlsafe(18).replace("-", "").replace("_", "")
    quote_no = make_no("Q")
    execute(
        """
        INSERT INTO quotes
        (quote_no, token, user_id, customer_name, customer_phone, city, days, budget,
         base_cost, markup_amount, final_price, valid_hours, status, plan_json, notes,
         created_at, expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'sent',%s,%s,%s,%s)
        """,
        (
            quote_no, token, user_id, payload.customerName.strip(), payload.customerPhone.strip(),
            payload.city, payload.days, _dec(payload.budget), base, markup, final,
            payload.validHours, json.dumps(payload.plan, ensure_ascii=False), payload.notes.strip(),
            now, expires,
        ),
    )
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise bad_request("报价单创建失败")
    return _payload(row)


@router.get("")
def list_quotes(user_id: int = Depends(get_current_user_id), limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    rows = fetch_all(
        "SELECT * FROM quotes WHERE user_id=%s ORDER BY id DESC LIMIT %s",
        (user_id, limit),
    )
    return {"items": [_payload(r) for r in rows]}


@router.post("/{token}/pay")
def pay_quote(token: str, payload: QuotePayRequest) -> dict[str, Any]:
    """客户在报价页发起支付；只允许该报价单已锁定的订单。"""
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    if row["status"] != "confirmed" or not row.get("order_id"):
        raise conflict("请先确认报价，再发起支付")
    if row.get("paid_at"):
        return {"quote": _payload(row, public=True), "payment": None}
    payment = pay_order(
        OrderPayRequest(paymentMethodCode=payload.paymentMethodCode, clientType="h5"),
        int(row["order_id"]),
        int(row["user_id"]),
    )
    return {"quote": _payload(row, public=True), "payment": payment}


@router.post("/{token}/pay/confirm")
def confirm_quote_payment(token: str) -> dict[str, Any]:
    """演示支付回调：把待支付订单置为已支付，并记录通知销售/供应商事件。"""
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    if row["status"] != "confirmed" or not row.get("order_id"):
        raise conflict("请先确认报价，再支付")
    if row.get("paid_at"):
        return _payload(row, public=True)

    payment = fetch_one(
        "SELECT * FROM payments WHERE order_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1",
        (row["order_id"], row["user_id"]),
    )
    if payment is None:
        payment = pay_order(
            OrderPayRequest(paymentMethodCode="alipay", clientType="h5"),
            int(row["order_id"]), int(row["user_id"]),
        )
        payment = fetch_one("SELECT * FROM payments WHERE id=%s", (payment["paymentId"],))
    if payment is None:
        raise conflict("支付单创建失败")
    if payment["status_code"] != "success":
        handle_payment_callback(
            PaymentCallbackRequest(
                paymentNo=payment["payment_no"], orderId=int(row["order_id"]),
                paymentMethodCode=payment["payment_method_code"],
                amount=payment["amount"], statusCode="success",
                paidAt=format_datetime(local_now()),
                channelTradeNo=f"DEMO-{payment['payment_no']}",
            ),
            DEMO_PAYMENT_SIGNATURE,
        )
    now = local_now()
    execute(
        """UPDATE quotes SET paid_at=%s, payment_no=%s, supplier_status='pending'
           WHERE id=%s""",
        (now, payment["payment_no"], row["id"]),
    )
    execute(
        """INSERT INTO quote_events (quote_id, event_type, actor, message, created_at)
           VALUES (%s,'payment.success','customer',%s,%s)""",
        (row["id"], f"客户支付成功，金额 {money(row['final_price'])} 元；已通知销售与供应商确认", now),
    )
    return _payload(fetch_one("SELECT * FROM quotes WHERE id=%s", (row["id"],)), public=True)


@router.post("/{token}/supplier-confirm")
def supplier_confirm(token: str, user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    if row["user_id"] != user_id:
        raise not_found("报价单不存在")
    if not row.get("paid_at"):
        raise conflict("客户尚未完成支付")
    if row.get("supplier_status") == "confirmed":
        return _payload(row)
    now = local_now()
    execute(
        "UPDATE quotes SET supplier_status='confirmed', supplier_confirmed_at=%s WHERE id=%s",
        (now, row["id"]),
    )
    execute(
        """INSERT INTO quote_events (quote_id, event_type, actor, message, created_at)
           VALUES (%s,'supplier.confirmed','supplier',%s,%s)""",
        (row["id"], "供应商已确认接单，行程进入履约准备", now),
    )
    return _payload(fetch_one("SELECT * FROM quotes WHERE id=%s", (row["id"],)))


@router.get("/{token}")
def get_quote(token: str) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    return _payload(row, public=True)


@router.patch("/{token}")
def update_price(token: str, payload: QuotePriceUpdate,
                 user_id: int = Depends(get_current_user_id)) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    if row["user_id"] != user_id:
        raise not_found("报价单不存在")
    if row["status"] != "sent":
        raise conflict("只有待确认报价可以改价")
    base = _dec(payload.baseCost)
    markup = _dec(payload.markupAmount)
    final = _dec(base + markup)
    if final < 0:
        raise bad_request("报价不能为负数")
    expires = local_now() + timedelta(hours=payload.validHours)
    execute(
        """UPDATE quotes SET base_cost=%s, markup_amount=%s, final_price=%s,
           valid_hours=%s, notes=%s, expires_at=%s WHERE id=%s""",
        (base, markup, final, payload.validHours, payload.notes.strip(), expires, row["id"]),
    )
    return _payload(fetch_one("SELECT * FROM quotes WHERE id=%s", (row["id"],)))


@router.post("/{token}/confirm")
def confirm_quote(token: str) -> dict[str, Any]:
    row = fetch_one("SELECT * FROM quotes WHERE token=%s", (token,))
    if not row:
        raise not_found("报价单不存在")
    row = _expire_if_needed(row)
    if row["status"] == "expired":
        raise conflict("报价单已过期，请联系顾问重新报价")
    if row["status"] == "confirmed":
        return _payload(row, public=True)

    now = local_now()
    order_no = make_no("TR")
    with db_cursor() as (_, cursor):
        # Lock the quote while creating the order so double-click cannot create two orders.
        cursor.execute("SELECT * FROM quotes WHERE id=%s FOR UPDATE", (row["id"],))
        locked = cursor.fetchone()
        if locked is None:
            raise not_found("报价单不存在")
        if locked["status"] == "confirmed":
            pass
        elif locked["status"] == "expired" or locked["expires_at"] < now:
            cursor.execute("UPDATE quotes SET status='expired' WHERE id=%s", (row["id"],))
            raise conflict("报价单已过期，请联系顾问重新报价")
        else:
            cursor.execute(
                """
                INSERT INTO orders (
                    order_no, user_id, order_type_code, status_code, currency_code,
                    goods_amount, marketing_discount_amount, coupon_discount_amount,
                    point_discount_amount, payable_amount, source_channel_code,
                    created_at, updated_at
                ) VALUES (%s,%s,'travel_package','pending_payment','CNY',%s,0,0,0,%s,'web',%s,%s)
                """,
                (order_no, locked["user_id"], locked["final_price"], locked["final_price"], now, now),
            )
            order_id = cursor.lastrowid
            cursor.execute(
                """
                INSERT INTO order_items (
                    order_id, user_id, traveler_id, product_type_code, product_id,
                    product_name, sale_amount, status_code, travel_time, created_at, updated_at
                ) VALUES (%s,%s,NULL,'travel_package',%s,%s,%s,'pending_payment',NULL,%s,%s)
                """,
                (
                    order_id, locked["user_id"], locked["id"],
                    f"{locked['city']} {locked['days']}天 AI 定制行程",
                    locked["final_price"], now, now,
                ),
            )
            cursor.execute(
                """UPDATE quotes SET status='confirmed', confirmed_at=%s,
                   order_id=%s, order_no=%s WHERE id=%s""",
                (now, order_id, order_no, locked["id"]),
            )
            cursor.execute(
                """INSERT INTO quote_events (quote_id, event_type, actor, message, created_at)
                   VALUES (%s,'quote.confirmed','customer',%s,%s)""",
                (locked["id"], f"客户确认报价，生成订单 {order_no}，价格已锁定", now),
            )
    return _payload(fetch_one("SELECT * FROM quotes WHERE id=%s", (row["id"],)), public=True)
