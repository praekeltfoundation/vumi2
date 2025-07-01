Implementing your own middleware
=================================

A middleware class provides three handler functions, one for processing each of the three kinds of messages transports, applications and dispatchers typically send and receive (i.e. inbound user messages, outbound user messages, event messages) and three enabled functions for each of the types of messages

Although transport and application middleware potentially both provide the same sets of handlers, the two make use of them in slightly different ways. Inbound messages and events are published by transports but consumed by applications while outbound messages are opposite.

Middleware is required to have the same interface as the BaseMiddleware class which is described  below.

BaseMiddleware
-----------------

``class vumi2.middlewares.BaseMiddleware(name, config)``

Common middleware base class.

This is a convenient definition of and set of common functionality for middleware classes. You need not subclass this and should not instantiate this directly.

The ``__init__()`` method should take exactly the following options so that your class can be instantiated from configuration in a standard way:

Parameters: 
-----------------
.. py:data:: config
   :type: dict[str]
    Dictionary of configuraiton items.

If you are subclassing this class, you should not override ``__init__()``. Custom setup should be done in 
``setup()`` instead. The config class can be overidden by replacing the ``config`` variable.

:py:data:`setup()`

Any custom setup may be done here.

Return type:	Deferred or None
Returns:	May return a deferred that is called when setup is complete.

``teardown()``
“Any custom teardown may be done here

Return type:	Deferred or None
Returns:	May return a Deferred that is called when teardown is complete

For each of inbound, outbound, and event message types, basemiddleware class implements two methods:

``{type}_enabled(self, connector_name)``: 
Checks the configuration to determine if the middleware is enabled for this message type and connector.

``handle_{type}(self, msg)``:
Processes the message before passing it to the next handler.

Example of a simple middleware implementation from ``vumi2.middlewares.logging:``

.. code-block:: Python

    from logging import getLevelNamesMapping, getLogger

    from attr import define

    from vumi2.middlewares.base import BaseMiddleware, BaseMiddlewareConfig


    @define
    class LoggingMiddlewareConfig(BaseMiddlewareConfig):
        log_level: str = "info"
        logger_name: str = __name__


    class LoggingMiddleware(BaseMiddleware):
        config: LoggingMiddlewareConfig

        async def setup(self):
            self.log_level = getLevelNamesMapping()[self.config.log_level.upper()]
            self.logger = getLogger(self.config.logger_name)

        def _log_msg(self, direction, msg, connector_name):
            self.logger.log(
                self.log_level,
                f"Processed {direction} message for {connector_name}: {msg}",
            )
            return msg

        async def handle_inbound(self, message, connector_name):
            return self._log_msg("inbound", message, connector_name)

        async def handle_outbound(self, message, connector_name):
            return self._log_msg("outbound", message, connector_name)

        async def handle_event(self, event, connector_name):
            return self._log_msg("event", event, connector_name)

How your middleware is used inside Vumi: 
----------------------------------------

While writing complex middleware, it may help to understand how a middleware class is used by Vumi transports and applications.

When a transport or application is started a list of middleware to load is read from the configuration. 
An instance of each piece of middleware is created and then ``setup()`` is called on each middleware object in 
order within the ``setup()`` of the worker

``middleware_{type}_handler function``  (e.g., middleware_outbound_handler) of BaseWorker of each message type. This function will:
Filter the middleware list based on connector_name and the middleware's ``{type}_enabled`` method.
Create a decorated handler function that sequentially applies each enabled middleware's ``handle_{type}`` method to the message.
Return the decorated handler. This decorated handler is then used in setting up the connection 
