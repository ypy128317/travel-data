from __future__ import annotations


def _quote_payload() -> dict:
    return {
        "customerName": "报价测试客户",
        "customerPhone": "13800000000",
        "city": "成都",
        "days": 3,
        "budget": 3000,
        "baseCost": 2000,
        "markupAmount": 300,
        "validHours": 48,
        "notes": "含 2 晚酒店，不含大交通",
        "plan": {
            "summary": "成都 3 天测试行程",
            "estimated_cost": 2000,
            "days_plan": [
                {"day": 1, "title": "抵达成都", "morning": "入住", "afternoon": "宽窄巷子",
                 "evening": "晚餐", "recommendations": []},
            ],
        },
    }


def test_create_share_and_confirm_quote(client, auth_headers):
    created = client.post("/api/quotes", headers=auth_headers, json=_quote_payload())
    assert created.status_code == 200, created.text
    quote = created.json()
    assert quote["quoteNo"].startswith("Q")
    assert quote["finalPrice"] == 2300
    assert quote["status"] == "sent"
    assert quote["quoteUrl"] == f"/quote/{quote['token']}"

    public = client.get(f"/api/quotes/{quote['token']}")
    assert public.status_code == 200
    assert public.json()["customerName"] == "报价测试客户"
    assert public.json()["finalPrice"] == 2300
    assert "customerPhone" not in public.json()

    confirmed = client.post(f"/api/quotes/{quote['token']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["confirmedAt"]
    assert confirmed.json()["orderId"]
    assert confirmed.json()["orderNo"].startswith("TR")
    assert confirmed.json()["priceLocked"] is True

    order = client.get(f"/api/v1/orders/{confirmed.json()['orderId']}", headers=auth_headers)
    assert order.status_code == 200, order.text
    assert order.json()["orderTypeCode"] == "travel_package"
    assert order.json()["payableAmount"] == 2300
    assert order.json()["statusCode"] == "pending_payment"
    payment = client.post(
        f"/api/quotes/{quote['token']}/pay",
        json={"paymentMethodCode": "alipay"},
    )
    assert payment.status_code == 200, payment.text
    assert payment.json()["payment"]["amount"] == 2300
    paid = client.post(f"/api/quotes/{quote['token']}/pay/confirm")
    assert paid.status_code == 200, paid.text
    assert paid.json()["paymentStatus"] == "paid"
    assert paid.json()["supplierStatus"] == "pending"
    assert any(event["type"] == "payment.success" for event in paid.json()["events"])
    supplier = client.post(f"/api/quotes/{quote['token']}/supplier-confirm", headers=auth_headers)
    assert supplier.status_code == 200, supplier.text
    assert supplier.json()["supplierStatus"] == "confirmed"

    listed = client.get("/api/quotes", headers=auth_headers)
    assert listed.status_code == 200
    assert any(item["token"] == quote["token"] for item in listed.json()["items"])


def test_quote_price_update_and_negative_rejected(client, auth_headers):
    created = client.post("/api/quotes", headers=auth_headers, json=_quote_payload()).json()
    updated = client.patch(
        f"/api/quotes/{created['token']}",
        headers=auth_headers,
        json={"baseCost": 2000, "markupAmount": -100, "validHours": 24, "notes": "优惠 100"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["finalPrice"] == 1900
    bad = client.patch(
        f"/api/quotes/{created['token']}",
        headers=auth_headers,
        json={"baseCost": 100, "markupAmount": -200, "validHours": 24},
    )
    assert bad.status_code == 400


def test_quote_missing_token_is_404(client):
    assert client.get("/api/quotes/not-a-real-token").status_code == 404
