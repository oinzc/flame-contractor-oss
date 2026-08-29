# -*- coding: utf-8 -*-
"""真实服务端到端测试:账号改名/删除、模板编辑/删除/启停、好友启停。

前置:隔离实例运行在 127.0.0.1:8765,使用临时数据库 e2e-test.db。
"""
import sys

import httpx

BASE = "http://127.0.0.1:8765/api"
client = httpx.Client(base_url=BASE, timeout=10)

failures: list[str] = []
passed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}  {detail}")


def expect_status(name: str, resp: httpx.Response, expected: int) -> None:
    check(name, resp.status_code == expected, f"got {resp.status_code}: {resp.text[:160]}")


# ============================================================ 清单1 好友启停
print("== 清单1 好友启停 ==")
account = client.post("/accounts", json={"displayName": "E2E主号"}).json()
target = client.post(
    "/targets", json={"accountId": account["id"], "nicknameSnapshot": "E2E好友"}
).json()

r = client.patch(f"/targets/{target['id']}", json={"enabled": False})
expect_status("暂停好友", r, 200)
check("暂停后 enabled=False", r.json()["enabled"] is False)
listing = client.get("/targets", params={"accountId": account["id"]}).json()
check("列表反映暂停状态", listing[0]["enabled"] is False)

r = client.patch(f"/targets/{target['id']}", json={"enabled": True})
check("重新启用", r.json()["enabled"] is True)

r = client.patch(f"/targets/{target['id']}", json={"streakDays": 9, "enabled": True})
check("启停与火花校准可同请求", r.status_code == 200 and r.json()["streakDays"] == 9, r.text[:120])
expect_status("暂停不存在的好友 404", client.patch("/targets/none", json={"enabled": False}), 404)

# ============================================================ 清单2 模板编辑/删除/启停
print("== 清单2 模板编辑/删除/启停 ==")
text_t = client.post("/templates", json={"name": "E2E早", "content": "早上好"}).json()
pool_t = client.post(
    "/templates",
    json={
        "name": "E2E池",
        "type": "pool",
        "poolItems": [{"content": "晚安", "weekdayMask": 0}, {"content": "好梦", "weekdayMask": 0}],
    },
).json()

# 文本编辑:内容变化 bump version
r = client.patch(f"/templates/{text_t['id']}", json={"content": "晚安 {{target_name}}"})
check("编辑内容成功", r.status_code == 200 and r.json()["content"].startswith("晚安"), r.text[:160])
check("内容变化 version 1→2", r.json()["version"] == 2)
r = client.patch(f"/templates/{text_t['id']}", json={"name": "E2E早安"})
check("仅改名不 bump version", r.json()["version"] == 2 and r.json()["name"] == "E2E早安")
expect_status("重名 409", client.patch(f"/templates/{text_t['id']}", json={"name": "E2E池"}), 409)
expect_status(
    "清空内容 422(合并校验)", client.patch(f"/templates/{text_t['id']}", json={"content": "  "}), 422
)
expect_status(
    "不支持变量 422", client.patch(f"/templates/{text_t['id']}", json={"content": "{{exec}}"}), 422
)

# 启停
r = client.patch(f"/templates/{text_t['id']}", json={"enabled": False})
check("模板停用且不 bump version", r.json()["enabled"] is False and r.json()["version"] == 2)
client.patch(f"/templates/{text_t['id']}", json={"enabled": True})

# 池候选整体替换
r = client.patch(
    f"/templates/{pool_t['id']}",
    json={
        "poolItems": [
            {"content": "晚安", "weekdayMask": 62},
            {"content": "做个好梦", "weekdayMask": 0},
            {"content": "早点休息", "weekdayMask": 0},
        ]
    },
)
check(
    "池候选替换为 3 条",
    r.status_code == 200 and [i["content"] for i in r.json()["poolItems"]] == ["晚安", "做个好梦", "早点休息"],
    r.text[:200],
)
check("池候选变化 bump version", r.json()["version"] == 2)
check(" weekday 位掩码生效", r.json()["poolItems"][0]["weekdayMask"] == 62)
before = {i["content"]: i["lastUsedAt"] for i in client.get("/templates").json()[1]["poolItems"]}
check("替换后池内容一致", set(before) == {"晚安", "做个好梦", "早点休息"}, str(before))

# 删除:无引用 204;被引用 409;404
expect_status("删除无引用文本模板 204", client.delete(f"/templates/{text_t['id']}"), 204)
bound = client.post(
    "/targets",
    json={"accountId": account["id"], "nicknameSnapshot": "E2E好友2", "templateId": pool_t["id"]},
).json()
expect_status("被好友绑定的模板删除 409", client.delete(f"/templates/{pool_t['id']}"), 409)
expect_status("删除已删模板 404", client.delete(f"/templates/{text_t['id']}"), 404)

# ============================================================ 清单3 账号改名/删除
print("== 清单3 账号改名/删除 ==")
other = client.post("/accounts", json={"displayName": "E2E小号"}).json()

r = client.patch(f"/accounts/{account['id']}", json={"displayName": "E2E大号"})
check("账号改名成功", r.status_code == 200 and r.json()["displayName"] == "E2E大号", r.text[:160])
expect_status("改名撞其他账号 409", client.patch(f"/accounts/{account['id']}", json={"displayName": "E2E小号"}), 409)
expect_status("改名空白 422", client.patch(f"/accounts/{other['id']}", json={"displayName": "   "}), 422)

# 删除:被好友引用 409 → 删好友 → 删除 204 → 列表消失 → 404
expect_status("有好友的账号删除 409", client.delete(f"/accounts/{account['id']}"), 409)
client.delete(f"/targets/{target['id']}")
client.delete(f"/targets/{bound['id']}")
r = client.delete(f"/accounts/{account['id']}")
expect_status("无引用账号删除 204", r, 204)
names = [a["displayName"] for a in client.get("/accounts").json()]
check("删除后账号消失", "E2E大号" not in names, str(names))
expect_status("重复删除 404", client.delete(f"/accounts/{account['id']}"), 404)

# 清理:删除另一账号与剩余模板,库回到空
r = client.delete(f"/accounts/{other['id']}")
expect_status("第二账号清理删除 204", r, 204)
client.delete(f"/templates/{pool_t['id']}")
check("数据库回到空", client.get("/accounts").json() == [] and client.get("/templates").json() == [])

print()
print(f"通过 {passed} 项,失败 {len(failures)} 项")
if failures:
    print("失败项:", failures)
    sys.exit(1)
