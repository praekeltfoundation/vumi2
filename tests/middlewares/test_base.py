from vumi2.middlewares.base import BaseMiddleware, BaseMiddlewareConfig


async def test_base_inbound_enabled():
    """
    Test for basemiddle ware when inbound connections are enabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=True,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.inbound_enabled("connection1") is False
    assert basemiddleware.inbound_enabled("connection2") is True


async def test_base_inbound_disabled():
    """
    Test for basemiddle ware when inbound connections are disabled on a connection
    """
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert basemiddleware.inbound_enabled("connection1") is False
    assert basemiddleware.inbound_enabled("connection2") is False


async def test_base_handle_inbound():
    config = BaseMiddlewareConfig(
        "vumi2.middlewares.base.BaseMiddleware",
        enable_for_connectors=["connection2"],
        inbound_enabled=False,
    )
    basemiddleware = BaseMiddleware(config)
    await basemiddleware.setup()
    assert await basemiddleware.handle_inbound("Hello") == "Hello"
