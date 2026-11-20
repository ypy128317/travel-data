"""分层数据校验。

每个校验函数返回一组可读的校验结果描述字符串（形如 "表名: N rows"），
由各 Layer 的 run() 逐条打印。校验以“发现数据落库且关键约束成立”为主，
不强依赖全量数据规模，以兼容 smoke 与 full 两种 profile。
"""

from ..db import db


def _count(table: str) -> int:
    row = db.fetch_one(f"SELECT COUNT(*) AS c FROM `{table}`")
    return int(row["c"]) if row else 0


def _count_sql(sql: str) -> int:
    row = db.fetch_one(sql)
    return int(row["c"]) if row else 0


def _row_counts(tables: list[str]) -> list[str]:
    return [f"{t}: {_count(t)} rows" for t in tables]


def validate_layer1() -> list[str]:
    checks = _row_counts(["areas", "currencies", "channels", "transport_hubs", "suppliers"])
    # 枢纽必须挂到城市级地区
    orphan = _count_sql(
        "SELECT COUNT(*) AS c FROM transport_hubs h "
        "JOIN areas a ON a.id = h.city_area_id WHERE a.level <> 2"
    )
    checks.append(f"transport_hubs 非城市级归属: {orphan}")
    # 供应商类型覆盖
    coverage = db.fetch_one(
        "SELECT COUNT(DISTINCT supplier_type_code) AS c FROM suppliers"
    )
    checks.append(f"supplier_type 覆盖数: {coverage['c'] if coverage else 0}")
    return checks


def validate_layer2() -> list[str]:
    checks = _row_counts([
        "hotels", "hotel_room_types", "hotel_booking_rules",
        "scenic_spots", "scenic_ticket_types", "scenic_booking_rules",
        "flight_routes", "train_routes", "bus_routes",
        "transfer_services", "transfer_service_area_rules",
    ])
    bad_price = _count_sql(
        "SELECT COUNT(*) AS c FROM transfer_service_area_rules "
        "WHERE price_amount < min_price_amount"
    )
    checks.append(f"接送规则非法价格(price<min)行: {bad_price}")
    return checks


def validate_layer3() -> list[str]:
    checks = _row_counts([
        "hotel_room_daily", "scenic_ticket_daily", "flight_departures",
        "flight_cabin_inventory", "train_departures", "train_seat_inventory",
        "bus_departures", "bus_seat_inventory", "transfer_capacity_calendar",
    ])
    inv_tables = [
        "hotel_room_daily", "scenic_ticket_daily", "flight_cabin_inventory",
        "train_seat_inventory", "bus_seat_inventory", "transfer_capacity_calendar",
    ]
    for t in inv_tables:
        bad = _count_sql(
            f"SELECT COUNT(*) AS c FROM `{t}` WHERE "
            "available_inventory + reserved_inventory + sold_inventory <> total_inventory"
        )
        checks.append(f"{t} 库存不等式的行: {bad}")
    return checks


def validate_layer4() -> list[str]:
    checks = _row_counts([
        "users", "user_profiles", "travelers", "member_accounts", "member_point_ledger",
    ])
    return checks


def validate_layer5() -> list[str]:
    checks = _row_counts([
        "coupon_templates", "user_coupons", "promotions", "promotion_rules",
        "promotion_bindings",
    ])
    return checks


def validate_layer6() -> list[str]:
    checks = _row_counts([
        "orders", "order_items", "payments", "refund_requests", "refund_records",
    ])
    return checks


def validate_stage7_acceptance() -> list[str]:
    """最终验收：核心链路各层关键表均有数据。"""
    core_tables = [
        "areas", "transport_hubs", "hotels", "scenic_spots", "flight_routes",
        "hotel_room_daily", "flight_departures", "users", "coupon_templates",
        "orders", "order_items", "payments",
    ]
    checks = []
    for t in core_tables:
        n = _count(t)
        if n <= 0:
            raise RuntimeError(f"验收失败：核心表 {t} 无数据")
        checks.append(f"{t}: {n} rows")
    return checks
