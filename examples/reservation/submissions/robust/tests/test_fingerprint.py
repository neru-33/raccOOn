from app.idempotency import fingerprint
from app.problems import Problem


def test_fingerprint_ignores_key_order():
    assert fingerprint("POST", "/v1/events/e/bookings", {"a": 1, "b": 2}) == fingerprint(
        "POST", "/v1/events/e/bookings", {"b": 2, "a": 1}
    )


def test_fingerprint_changes_with_body_or_path():
    base = fingerprint("POST", "/v1/events/e/bookings", {"userId": "u1"})
    assert base != fingerprint("POST", "/v1/events/e/bookings", {"userId": "u2"})
    assert base != fingerprint("POST", "/v1/events/other/bookings", {"userId": "u1"})


def test_problem_body_follows_rfc9457_members():
    body = Problem(409, "sold-out", "Sold out", "no seats").body("/v1/events/e/bookings")
    assert body == {
        "type": "/problems/sold-out",
        "title": "Sold out",
        "status": 409,
        "detail": "no seats",
        "instance": "/v1/events/e/bookings",
    }
