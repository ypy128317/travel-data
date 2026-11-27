"""旅游平台业务数据服务入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from .config import APP_PORT, AMAP_KEY, AMAP_SECURITY_CODE
from .database import fetch_all, fetch_one
from .routers import admin, ai, guides, orders, products, refunds, users

app = FastAPI(
    title="云游旅行 · AI 旅行规划 API",
    description="面向游客的 AI 行程规划、商品查询、下单、支付与售后接口。",
    version="0.1.0",
)

app.include_router(products.router)
app.include_router(users.router)
app.include_router(orders.router)
app.include_router(refunds.router)
app.include_router(ai.router)
app.include_router(guides.router)
app.include_router(admin.router)



STATIC_DIR = Path(__file__).resolve().parent / "static"


def _no_cache(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.get("/", tags=["meta"], include_in_schema=False)
def index() -> Response:
    """可视化数据浏览首页。"""
    return _no_cache(FileResponse(STATIC_DIR / "index.html"))


@app.get("/app", tags=["meta"], include_in_schema=False)
def app_page() -> Response:
    return _no_cache(FileResponse(STATIC_DIR / "index.html"))


@app.get("/app.js", tags=["meta"], include_in_schema=False)
def app_script() -> Response:
    return _no_cache(FileResponse(STATIC_DIR / "app.js", media_type="application/javascript"))


@app.get("/admin", tags=["meta"], include_in_schema=False)
def admin_page() -> Response:
    """运营管理后台（工作人员端），与游客端首页隔离。"""
    return _no_cache(FileResponse(STATIC_DIR / "admin.html"))


@app.get("/admin.js", tags=["meta"], include_in_schema=False)
def admin_script() -> Response:
    return _no_cache(FileResponse(STATIC_DIR / "admin.js", media_type="application/javascript"))



@app.get("/map", tags=["meta"], include_in_schema=False, response_class=HTMLResponse)
def map_page() -> HTMLResponse:
    """独立线路地图页：Key 从 .env 读取后注入，前端源码不含真实 Key。"""
    if not AMAP_KEY or not AMAP_SECURITY_CODE:
        return HTMLResponse(
            """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>地图未配置</title><style>body{font-family:'Microsoft YaHei',sans-serif;max-width:640px;
margin:60px auto;padding:24px;line-height:1.8;color:#1f2937}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}
a{color:#2563eb}</style></head><body><h2>🗺️ 高德地图 Key 尚未配置</h2>
<p>请到 <a href="https://console.amap.com/dev/key/app" target="_blank">高德开放平台控制台</a>
免费申请 Key：创建应用 → 添加 Key，服务平台选 <b>Web端(JS API)</b>，会同时拿到 <b>Key</b> 和 <b>安全密钥 securityJsCode</b>（JS API 2.0 两者缺一不可）。然后填入项目根目录的 <code>.env</code> 文件：</p>
<pre>AMAP_KEY=你的Key
AMAP_SECURITY_CODE=你的安全密钥</pre><p>保存后重启服务，再访问本页即可。</p></body></html>""",
            status_code=200,
        )
    html = (STATIC_DIR / "map_template.html").read_text(encoding="utf-8")
    html = html.replace("__AMAP_KEY__", AMAP_KEY).replace("__AMAP_SECURITY_CODE__", AMAP_SECURITY_CODE)
    return HTMLResponse(html)


@app.get("/api/meta/overview", tags=["meta"], include_in_schema=False)
def overview() -> dict[str, dict[str, int]]:
    """核心业务表数据规模概览。"""
    labels = {
        "areas": "地区",
        "transport_hubs": "交通枢纽",
        "suppliers": "供应商",
        "hotels": "酒店",
        "scenic_spots": "景点",
        "flight_departures": "机票班次",
        "train_departures": "火车班次",
        "bus_departures": "汽车班次",
        "transfer_services": "接送服务",
        "users": "用户",
        "coupon_templates": "优惠券",
        "orders": "订单",
        "payments": "支付单",
        "refund_requests": "退款申请",
        "guides": "真人导游",
        "guide_bookings": "导游预约",
    }
    stats: dict[str, int] = {}
    for table, label in labels.items():
        row = fetch_all(f"SELECT COUNT(*) AS c FROM `{table}`")
        stats[label] = int(row[0]["c"]) if row else 0
    return {"stats": stats}


@app.get("/api/meta/refundable-items", tags=["meta"], include_in_schema=False)
def refundable_items(userId: int = 1) -> dict[str, list[dict]]:
    """返回指定用户当前可申请的退款明细，供页面选择，避免手填无效 ID。"""
    rows = fetch_all(
        """
        SELECT oi.id AS itemId, oi.order_id AS orderId, oi.product_name AS productName,
               oi.sale_amount AS saleAmount, oi.status_code AS itemStatusCode,
               COALESCE(r.total, 0) AS refundedAmount,
               (oi.sale_amount - COALESCE(r.total, 0)) AS remainingAmount
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        LEFT JOIN (
            SELECT order_item_id, SUM(amount) AS total
            FROM refund_records WHERE status_code = 'success' GROUP BY order_item_id
        ) r ON r.order_item_id = oi.id
        WHERE o.user_id = %s AND o.status_code = 'paid'
          AND oi.status_code IN ('paid', 'ticketed', 'completed')
          AND NOT EXISTS (
              SELECT 1 FROM refund_requests rr
              WHERE rr.order_item_id = oi.id AND rr.user_id = %s
                AND rr.status_code IN ('pending', 'approved')
          )
          AND (oi.sale_amount - COALESCE(r.total, 0)) > 0
        ORDER BY o.id, oi.id LIMIT 50
        """,
        (userId, userId),
    )
    result: list[dict] = []
    for row in rows:
        result.append(
            {
                "itemId": row["itemId"],
                "orderId": row["orderId"],
                "productName": row["productName"],
                "saleAmount": float(row["saleAmount"]),
                "itemStatusCode": row["itemStatusCode"],
                "remainingAmount": float(row["remainingAmount"]),
            }
        )
    return {"items": result}


class OrderSuggestBody(BaseModel):
    type: str
    refId: int
    date: str
    returnDate: str | None = None
    userId: int = 1
    # 展示给用户的具体SKU/舱位ID：传入后锁定该商品，保证展示价=实付价
    inventoryId: int | None = None


def _first_traveler(user_id: int) -> int | None:
    row = fetch_one(
        "SELECT id FROM travelers WHERE user_id = %s AND status_code = 'active' ORDER BY id LIMIT 1",
        (user_id,),
    )
    return int(row["id"]) if row else None


@app.post("/api/meta/order-suggest", tags=["meta"], include_in_schema=False)
def order_suggest(body: OrderSuggestBody) -> dict[str, Any]:
    """把搜索结果（商品类型+主表ID）转换为可直接下单的订单明细参数。"""
    t, rid, date = body.type, body.refId, body.date
    item: dict[str, Any] | None = None
    product_type: str = ""

    if t == "hotel":
        product_type = "hotel_room"
        end = body.returnDate or date
        row = fetch_one(
            """
            SELECT rt.id AS pid, CONCAT(h.hotel_name,'-',rt.room_type_name) AS pname,
                   COUNT(d.id) AS nights
            FROM hotel_room_types rt
            JOIN hotels h ON h.id = rt.hotel_id AND h.status_code = 'active'
            JOIN hotel_room_daily d ON d.room_type_id = rt.id
              AND d.business_date >= %s AND d.business_date < %s
              AND d.status_code='active' AND d.available_inventory > 0
            WHERE rt.hotel_id = %s AND rt.status_code='active'
            GROUP BY rt.id, rt.room_type_name
            HAVING nights = DATEDIFF(%s, %s)
            ORDER BY rt.id LIMIT 1
            """,
            (date, end, rid, end, date),
        )
        if row:
            item = {
                "productTypeCode": product_type, "productId": row["pid"],
                "productName": row["pname"], "quantity": 1,
                "checkInDate": date, "checkOutDate": end,
            }
    elif t == "scenic":
        product_type = "scenic_ticket"
        row = fetch_one(
            """
            SELECT t.id AS pid, CONCAT(s.scenic_name,'-',t.ticket_type_name) AS pname
            FROM scenic_ticket_types t
            JOIN scenic_spots s ON s.id=t.scenic_spot_id AND s.status_code='active'
            JOIN scenic_ticket_daily d ON d.ticket_type_id=t.id
              AND d.business_date=%s AND d.status_code='active' AND d.available_inventory>0
            WHERE t.scenic_spot_id=%s AND t.status_code='active'
            GROUP BY t.id ORDER BY t.id LIMIT 1
            """,
            (date, rid),
        )
        if row:
            item = {
                "productTypeCode": product_type, "productId": row["pid"],
                "productName": row["pname"], "quantity": 1,
                "travelTime": date + " 09:00:00",
            }
    elif t in ("flight", "train", "bus"):
        product_type = {"flight": "flight_cabin", "train": "train_seat", "bus": "bus_seat"}[t]
        inv = {"flight": "flight_cabin_inventory", "train": "train_seat_inventory", "bus": "bus_seat_inventory"}[t]
        dep = {"flight": "flight_departures", "train": "train_departures", "bus": "bus_departures"}[t]
        fk = {"flight": "flight_departure_id", "train": "train_departure_id", "bus": "bus_departure_id"}[t]
        rk = {"flight": "flight_route_id", "train": "train_route_id", "bus": "bus_route_id"}[t]
        rt = {"flight": "flight_routes", "train": "train_routes", "bus": "bus_routes"}[t]
        name_col = {"flight": "flight_no", "train": "train_no", "bus": "route_name"}[t]
        cls_col = {"flight": "cabin_class_code", "train": "seat_class_code", "bus": "seat_class_code"}[t]
        # 锁定展示时看到的具体舱位：传入 inventoryId 且仍可售时用之，否则回退价格最低
        if body.inventoryId:
            row = fetch_one(
                f"""
                SELECT i.id AS pid, CONCAT(r.{name_col},'-',i.{cls_col}) AS pname,
                       d.departure_time AS dep_time
                FROM {inv} i
                JOIN {dep} d ON d.id=i.{fk} AND d.status_code='scheduled'
                JOIN {rt} r ON r.id=d.{rk} AND r.status_code='active'
                WHERE i.id=%s AND d.id=%s AND i.status_code='active' AND i.available_inventory>0
                LIMIT 1
                """,
                (body.inventoryId, rid),
            )
        else:
            row = fetch_one(
                f"""
                SELECT i.id AS pid, CONCAT(r.{name_col},'-',i.{cls_col}) AS pname,
                       d.departure_time AS dep_time
                FROM {inv} i
                JOIN {dep} d ON d.id=i.{fk} AND d.status_code='scheduled'
                JOIN {rt} r ON r.id=d.{rk} AND r.status_code='active'
                WHERE d.id=%s AND i.status_code='active' AND i.available_inventory>0
                ORDER BY i.sale_price_amount ASC, i.id ASC LIMIT 1
                """,
                (rid,),
            )
        if row:
            dep_time = row["dep_time"].strftime("%Y-%m-%d %H:%M:%S")
            traveler = _first_traveler(body.userId)
            item = {
                "productTypeCode": product_type, "productId": row["pid"],
                "productName": row["pname"], "quantity": 1,
                "travelTime": dep_time,
                "travelerIds": [traveler] if traveler else [],
            }
    elif t == "transfer":
        product_type = "transfer_service"
        row = fetch_one(
            "SELECT id, service_name FROM transfer_services WHERE id=%s AND status_code='active'",
            (rid,),
        )
        if row:
            item = {
                "productTypeCode": product_type, "productId": row["id"],
                "productName": row["service_name"], "quantity": 1,
                "travelTime": date + " 09:00:00",
            }

    if item is None:
        return {"ok": False, "message": "该商品当前无可售库存，请换一个或换个日期"}
    return {
        "ok": True,
        "orderTypeCode": product_type,
        "item": item,
        "needsTraveler": product_type in ("flight_cabin", "train_seat", "bus_seat"),
    }


@app.get("/api/meta/cities", tags=["meta"], include_in_schema=False)
def meta_cities() -> dict[str, list[dict[str, Any]]]:
    """返回数据库全部城市（地区 level=2），供页面城市下拉使用。"""
    rows = fetch_all(
        "SELECT id, area_name, area_full_name FROM areas "
        "WHERE level=2 AND status_code='active' ORDER BY id"
    )
    cities: list[dict[str, Any]] = []
    for r in rows:
        parts = (r["area_full_name"] or "").split("/")
        name = r["area_name"]
        if name == "市辖区" and parts:
            name = parts[0]
        cities.append({"id": int(r["id"]), "name": name, "fullName": r["area_full_name"]})
    return {"cities": cities}


@app.get("/api/meta/kb/stats", tags=["meta"], include_in_schema=False)
def kb_stats() -> dict[str, Any]:
    from .knowledge import KB
    return KB.stats()


@app.get("/api/meta/kb/search", tags=["meta"], include_in_schema=False)
def kb_search(q: str = "", city: str = "", type: str = "", topK: int = 5) -> dict[str, Any]:
    from .knowledge import KB
    items = KB.search(q, city=city or None, content_type=type or None, top_k=topK)
    return {"total": len(items), "items": items}


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=APP_PORT, reload=False)



