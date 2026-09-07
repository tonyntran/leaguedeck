TEST_PASSWORD = "testpassword123"


def test_login_with_correct_password_sets_session_cookie(client):
    resp = client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert resp.status_code == 200
    assert "leaguedeck_session" in resp.cookies


def test_login_with_wrong_password_is_rejected(client):
    resp = client.post("/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 401


def test_logout_clears_session_cookie(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
