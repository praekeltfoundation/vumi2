Junebug Message API
-------------------

This application provides bidirectional messaging over HTTP, primarily intended
for integration with RapidPro.

.. todo::
   A high-level description of the API, probably based on existing Junebug docs.


Configuration
^^^^^^^^^^^^^

The application has the following configuration options:

All the configuration items from the base worker, which you can find at :ref:`base-worker-configuration`

connector_name (str)
    The name of the AMQP queue to publish outbound message on, and to consume inbound messages and events from. Required.
mo_message_url (str)
    The URL to send HTTP POST requests to for inbound messages. If a username and password are included in the URL, they will be used for basic authentication. Required.
mo_message_url_auth_token (str)
    The authorization token to use for inbound message HTTP requests. Token authentication will only be used if a token is provided. Defaults to no token.
base_url_path (str)
    Base URL path for outbound message HTTP requests. Outbound message requests must be POSTed to `<base_url_path>/messages`. For compatibility with existing Junebug API clients, set this to `/channels/<channel_id>`. Defaults to an empty string.
transport_type (str)
    The transport_type to use for non-reply outbound messages. Defaults to `sms`.
default_from_addr (str)
    The from address to be used for non-reply outbound messages that don't specify a from address. By default, non-reply outbound messages are required to specify a from address.
allow_expired_replies (bool)
    If `True`, outbound messages with both `to` and `reply_to` set will be sent as non-reply messages if the `reply_to` message can't be found. Defaults to `False`.
state_cache_class (str)
    The python path to the class used for the application state cache. This class is resposible for caching inbound messages for outbound replies and event info for outbound messages. Defaults to `vumi2.applications.junebug_message_api.junebug_state_cache.InMemorySmppCache`, which stores the data in memory. See :ref:`junebug-state-caches` for a list of state caches.
state_cache_config (dict)
    The config that `state_cache_class` requires. See :ref:`junebug-state-caches` for details.
request_timeout (float)
    The maximum time allowed (in seconds) for outbound message request handling. Defaults to 240.
mo_message_url_timeout (float)
    Maximum time allowed (in seconds) for inbound message HTTP requests. Defaults to 10.
event_url_timeout (float)
    Maximum time allowed (in seconds) for event HTTP requests. Defaults to 10.

.. todo::
   Document API.


.. _junebug-state-caches:

Junebug state caches
^^^^^^^^^^^^^^^^^^^^

A Junebug state cache stores everything required for event delivery and reply messages.

In memory state cache
"""""""""""""""""""""

`vumi2.applications.junebug_message_api.junebug_state_cache.InMemorySmppCache`

This is a state cache implementation that stores the data in memory. Because of this, it is not suitable to share the data across multiple processes, and it will not survive process restarts.

It has the following configuration fields:

timeout (int)
    The maximum amount of time (in seconds) to keep inbound messages (for replies) and event delivery informnation. Defaults to 24 hours.
