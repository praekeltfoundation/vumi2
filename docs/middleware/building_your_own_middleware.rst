Implementing your own middleware
=================================

A middleware class provides three handler functions, one for processing each of the three kinds of messages transports, applications and dispatchers typically send and receive (i.e. inbound user messages, outbound user messages, event messages) and three enabled functions for each of the types of messages

Although transport and application middleware potentially both provide the same sets of handlers, the two make use of them in slightly different ways. Inbound messages and events are published by transports but consumed by applications while outbound messages are opposite.

A middleware is required to subclass BaseMiddleware. This is a convenient definition of and set of common functionality for middleware classes. However you should not override :py:meth:`__init__()`. Custom setup should be done in 
:py:meth:`setup()` instead (if required). The config class can be overidden by replacing the :py:meth:`config` variable.
You should also overwrite :py:meth:`handle_{type}(self, msg, connection)` in most cases while using :py:meth:`{type}_enabled(self, connector_name)` from the base class


See logging and unidecoder examples 

How your middleware is used inside Vumi: 
----------------------------------------

While writing complex middleware, it may help to understand how a middleware class is used by Vumi transports and applications.

When a transport or application is started a list of middleware to load is read from the configuration. 
An instance of each piece of middleware is created and then :py:meth:`setup()` is called on each middleware object in 
order within the :py:meth:`setup()`  of the worker

:py:meth:`middleware_{type}_handler` function  (e.g., middleware_outbound_handler) of BaseWorker of each message type. This function will:
Filter the middleware list based on connector_name and the middleware's :py:meth:`{type}_enabled` method.
Create a decorated handler function that sequentially applies each enabled middleware's :py:meth:`handle_{type}` method to the message.
Return the decorated handler. This decorated handler is then used in setting up the connection 
