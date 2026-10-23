def test_register_duplicate(client):
    body = {"email": "x@y.com", "password": "password123"}
    assert client.post("/auth/register", json=body).status_code == 201
    assert client.post("/auth/register", json=body).status_code == 409


def test_login_wrong_password(client):
    client.post("/auth/register", json={"email": "x@y.com", "password": "password123"})
    r = client.post("/auth/login", data={"username": "x@y.com", "password": "nope"})
    assert r.status_code == 401


def test_links_require_auth(client):
    assert client.post("/links", json={"target_url": "https://example.com"}).status_code == 401


def test_create_redirect_and_stats(client, auth):
    r = client.post("/links", json={"target_url": "https://example.com/page"}, headers=auth)
    assert r.status_code == 201
    code = r.json()["code"]
    for _ in range(3):
        resp = client.get(f"/{code}", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "https://example.com/page"
    stats = client.get(f"/links/{code}/stats", headers=auth).json()
    assert stats["total_clicks"] == 3
    assert stats["last_7_days"][0]["clicks"] == 3


def test_custom_alias_conflict(client, auth):
    body = {"target_url": "https://example.com", "custom_alias": "hello"}
    assert client.post("/links", json=body, headers=auth).status_code == 201
    assert client.post("/links", json=body, headers=auth).status_code == 409


def test_reserved_alias(client, auth):
    body = {"target_url": "https://example.com", "custom_alias": "docs"}
    assert client.post("/links", json=body, headers=auth).status_code == 400


def test_unknown_code_404(client):
    assert client.get("/doesnotexist", follow_redirects=False).status_code == 404


def test_delete_link(client, auth):
    code = client.post("/links", json={"target_url": "https://example.com"}, headers=auth).json()["code"]
    assert client.delete(f"/links/{code}", headers=auth).status_code == 204
    assert client.get(f"/{code}", follow_redirects=False).status_code == 404


def test_other_user_cannot_see_stats(client, auth):
    code = client.post("/links", json={"target_url": "https://example.com"}, headers=auth).json()["code"]
    client.post("/auth/register", json={"email": "c@d.com", "password": "password123"})
    t = client.post("/auth/login", data={"username": "c@d.com", "password": "password123"}).json()["access_token"]
    assert client.get(f"/links/{code}/stats", headers={"Authorization": f"Bearer {t}"}).status_code == 404
