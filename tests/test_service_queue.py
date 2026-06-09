from app.services.blink import service


def test_service_exposes_queue_submit():
    async def coro():
        return 42
    assert service.submit(1, coro, timeout=5) == 42
