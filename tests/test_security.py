from saburo import config, security


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_limiter_locks_one_client_and_unlocks_after_window():
    clock = Clock()
    lim = security.LoginLimiter(clock)
    for _ in range(security.PER_CLIENT_LIMIT):
        assert lim.retry_after("a") == 0
        lim.failed("a")
    assert lim.retry_after("a") > 0
    assert lim.retry_after("b") == 0  # 他の人は締め出さない
    clock.t += security.PER_CLIENT_WINDOW + 1
    assert lim.retry_after("a") == 0


def test_limiter_success_resets_client():
    lim = security.LoginLimiter(Clock())
    for _ in range(security.PER_CLIENT_LIMIT - 1):
        lim.failed("a")
    lim.succeeded("a")
    lim.failed("a")
    assert lim.retry_after("a") == 0


def test_limiter_global_lock_against_many_addresses():
    lim = security.LoginLimiter(Clock())
    for i in range(security.GLOBAL_LIMIT):
        lim.failed(f"ip{i}")
    assert lim.retry_after("brand-new") > 0


def test_forwarded_ip_only_trusted_from_local_tunnel():
    h = {"cf-connecting-ip": "203.0.113.5"}
    assert security.client_address("127.0.0.1", h) == "203.0.113.5"
    assert security.client_address("198.51.100.1", h) == "198.51.100.1"  # 外から偽装されても信じない
    assert security.is_https("http", "127.0.0.1", {"x-forwarded-proto": "https"})
    assert not security.is_https("http", "198.51.100.1", {"x-forwarded-proto": "https"})


def test_session_token_expiry_rotation_and_password_change(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "correct horse battery staple")
    token = security.new_session_token(now=10_000)
    assert security.check_session_token(token, now=10_000 + 60)
    assert not security.check_session_token(token, now=10_000 + security.SESSION_MAX_AGE + 1)
    assert not security.check_session_token(token.replace(".", ".0"), now=10_060)
    assert not security.check_session_token("garbage", now=10_060)
    monkeypatch.setattr(config, "PASSWORD", "another password entirely")
    assert not security.check_session_token(token, now=10_060)  # パスワード変更で無効
    token = security.new_session_token(now=10_000)
    security.rotate_secret()
    assert not security.check_session_token(token, now=10_060)  # 全端末ログアウトで無効


def test_password_rules(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", "")
    assert security.password_problem(public=False)
    monkeypatch.setattr(config, "PASSWORD", "change-me")
    assert security.password_problem(public=False)
    monkeypatch.setattr(config, "PASSWORD", "short1")
    assert security.password_problem(public=False) is None
    assert security.password_problem(public=True)
    monkeypatch.setattr(config, "PASSWORD", "long-enough-passphrase")
    assert security.password_problem(public=True) is None
