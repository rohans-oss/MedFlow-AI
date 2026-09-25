from tests.conftest import PASSWORD


def test_login_sets_http_only_cookies_and_me_works(client, world):
    r = client.post("/api/auth/login", json={"email": "admin@test.demo", "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "admin"
    assert "users:manage" in body["user"]["permissions"]
    set_cookie = r.headers.get_list("set-cookie")
    assert any(c.startswith("access_token=") and "HttpOnly" in c for c in set_cookie)
    assert any(c.startswith("refresh_token=") and "HttpOnly" in c and "Path=/api/auth" in c for c in set_cookie)
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "admin@test.demo"


def test_bearer_token_also_works(client, world):
    token = client.post("/api/auth/login", json={"email": "viewer@test.demo", "password": PASSWORD}).json()["access_token"]
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_wrong_password_and_unknown_user_are_indistinguishable(client, world):
    a = client.post("/api/auth/login", json={"email": "admin@test.demo", "password": "nope"})
    b = client.post("/api/auth/login", json={"email": "ghost@test.demo", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_inactive_user_cannot_login(client, world, db):
    u = world["users"]["viewer"]
    u.is_active = False
    db.commit()
    assert client.post("/api/auth/login", json={"email": u.email, "password": PASSWORD}).status_code == 401


def test_refresh_and_logout_revokes_refresh_token(client, world):
    client.post("/api/auth/login", json={"email": "admin@test.demo", "password": PASSWORD})
    old_refresh = client.cookies.get("refresh_token")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.set("refresh_token", old_refresh, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401


def test_login_rate_limited(client, world):
    for _ in range(10):
        client.post("/api/auth/login", json={"email": "admin@test.demo", "password": "bad"})
    r = client.post("/api/auth/login", json={"email": "admin@test.demo", "password": PASSWORD})
    assert r.status_code == 429


def test_change_password(login, world):
    c = login("viewer@test.demo")
    r = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "NewPassw0rd"})
    assert r.status_code == 200
    c.cookies.clear()
    assert c.post("/api/auth/login", json={"email": "viewer@test.demo", "password": "NewPassw0rd"}).status_code == 200


def test_unauthenticated_requests_rejected(client, world):
    for path in ("/api/inventory", "/api/dashboard/summary", "/api/suppliers", "/api/alerts"):
        assert client.get(path).status_code == 401
