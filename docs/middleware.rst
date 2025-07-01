Middleware
==========
Middleware provides additional functionality that can be attached to any existing transport, 
application or dispatcher worker. For example, middleware could log inbound and outbound messages, decode a unicoded message,
store delivery reports in a database or modify a message.

Attaching middleware to your worker is fairly straight forward. Just extend your YAML configuration file with lines like:Attaching middleware to your worker is fairly straight forward. Just extend your YAML configuration file with lines like:

 .. code-block:: YAML

    middlewares:
    - class_path: vumi2.middlewares.unidecoder.Unidecoder
        enable_for_connectors: ["mc_qa_expressway"]
        outbound_enabled: true

The middleware section contains a list of middleware items. 
Each item contains a :py:data:`class_path` class_path which is the full Python path to the class implementing the middleware, 
:py:data:`enable_for_connectors` enable_for_connectors this is the list of transports that the middleware will run on, 
and :py:data:`{type}_enabled`  fields which describes which type of message the middleware is enabled on (inbound, outbound or event)

Multiple layers of middleware may be specified as follows:

.. code-block:: YAML
    
    middlewares:
    - class_path: vumi2.middlewares.unidecoder.Unidecoder
        enable_for_connectors: ["mc_qa_expressway"]
        outbound_enabled: true
    - class_path: vumi2.middlewares.logging.LoggingMiddlewarer
        enable_for_connectors: ["mc_qa_expressway"]
        outbound_enabled: true
        inbound_enabled: true
        event_enabled: true

You can think of the layers of middleware sitting on top of the underlying transport or application worker. 
Messages being consumed by the worker enter from the top and are processed by the middleware in the order you 
have defined them and eventually reach the worker at the bottom. Messages published by the worker start at the 
bottom and travel up through the layers of middleware before finally exiting the middleware at the top.

Adding your middleware:

.. toctree::
    :maxdepth: 2

    middleware/building_your_own_middleware