from __future__ import annotations


def test_create_list_and_claim_inquiry(client, auth_headers):
    created = client.post(
        "/api/inquiries",
        headers=auth_headers,
        json={
            "customerName": "询价测试",
            "customerPhone": "13800000000",
            "city": "成都",
            "days": 3,
            "budget": 3000,
            "preferences": "亲子",
            "userId": 1,
            "plan": {"summary": "测试行程", "days_plan": [{"day": 1, "title": "抵达"}]},
        },
    )
    assert created.status_code == 200, created.text
    inquiry = created.json()
    assert inquiry["status"] == "new"
    assert inquiry["plan"]["summary"] == "测试行程"

    listed = client.get("/api/inquiries", headers=auth_headers)
    assert listed.status_code == 200
    assert any(item["id"] == inquiry["id"] for item in listed.json()["items"])

    claimed = client.post(f"/api/inquiries/{inquiry['id']}/claim", headers=auth_headers)
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "contacted"
