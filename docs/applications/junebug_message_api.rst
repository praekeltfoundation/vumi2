Junebug Message API
-------------------

This application provides bidirectional messaging over HTTP, primarily intended
for integration with RapidPro.

Configuration
^^^^^^^^^^^^^

The application has the following configuration options:

**Common to all vumi workers**

All the configuration items from the base worker (see :ref:`base-worker-configuration` for details) are available. However, :py:data:`http_bind` is required rather than optional.

**Junebug Message API specific**

.. py:currentmodule:: vumi2.applications.junebug_message_api

.. py:data:: connector_name
   :type: str

   The name of the AMQP queue to publish outbound message on, and to consume inbound messages and events from. Required.

.. py:data:: mo_message_url
   :type: str

   The URL to send HTTP POST requests to for inbound messages. If a username and password are included in the URL, they will be used for basic authentication. Required.

.. py:data:: mo_message_url_auth_token
   :type: str

   The authorization token to use for inbound message HTTP requests. Token authentication will only be used if a token is provided. Defaults to no token.

.. py:data:: default_event_url
   :type: str

   The default URL to send HTTP POST requests to for events that don't have stored event delivery info from the associated outbound message. If unset, such events cannot be delivered and will be logged instead.

.. py:data:: default_event_auth_token
   :type: str

   The authorization token to use for events sent to the :py:data:`default_event_url`. Token authentication will only be used if a token is provided. Defaults to no token.

.. py:data:: base_url_path
   :type: str

   Base URL path for outbound message HTTP requests. Outbound message requests must be POSTed to ``<base_url_path>/messages``. For compatibility with existing Junebug API clients, set this to ``/channels/<channel_id>``. Defaults to an empty string.

.. py:data:: transport_type
   :type: str

   The transport_type to use for non-reply outbound messages. Defaults to ``sms``.

.. py:data:: default_from_addr
   :type: str

   The from address to be used for non-reply outbound messages that don't specify a from address. By default, non-reply outbound messages are required to specify a from address.

.. py:data:: allow_expired_replies
   :type: bool

   If ``True``, outbound messages with both ``to`` and ``reply_to`` set will be sent as non-reply messages if the ``reply_to`` message can't be found. Defaults to ``False``.

.. py:data:: state_cache_class
   :type: str

   The python path to the class used for the application state cache. This class is resposible for caching inbound messages for outbound replies and event info for outbound messages. Defaults to ``vumi2.applications.junebug_message_api.junebug_state_cache.InMemorySmppCache``, which stores the data in memory. See :ref:`junebug-state-caches` for a list of state caches.

.. py:data:: state_cache_config
   :type: dict

   The config that :py:data:`state_cache_class` requires. See :ref:`junebug-state-caches` for details.

.. py:data:: request_timeout
   :type: float

   The maximum time allowed (in seconds) for outbound message request handling. Defaults to 240.

.. py:data:: mo_message_url_timeout
   :type: float

   Maximum time allowed (in seconds) for inbound message HTTP requests. Defaults to 10.

.. py:data:: event_url_timeout
   :type: float

   Maximum time allowed (in seconds) for event HTTP requests. Defaults to 10.

How it works
^^^^^^^^^^^^

The application worker listens on HTTP for outbound messages from the external application and forwards them over AMQP to a router or transport. Inbound messages and events are forwarded to the external application over HTTP.

Outbound message API
""""""""""""""""""""

.. http:post:: <base_url_path>/messages

   Send an outbound (mobile terminated) message.

   :<json str to: The address (e.g. MSISDN) to send the message to. If :py:data:`allow_expired_replies` is set, the ``to`` parameter is used as a fallback in case the value of the ``reply_to`` parameter does not resolve to an inbound message.

   :<json str from: The address the message is from. May be ``null`` if :py:data:`default_from_addr` is configured.

   :<json str group: If supported by the transport or router, the group to send the messages to. Not required, and may be ``null``.

   :<json str reply_to: The uuid of the message being replied to if this is a response to a previous message. Important for session-based transports like USSD. Optional.
      If :py:data:`allow_expired_replies` is set, ``to`` and ``from`` will be used as a fallback in case ``reply_to`` does not resolve to an inbound message.
      The default settings allow 24 hours to reply to a message, after which an error will be returned.

   :<json str content: The text content of the message. Required.

   :<json str event_url: URL to call for status events (e.g. acknowledgements and delivery reports) related to this message. The default settings allow 24 hours for events to arrive, after which they will no longer be forwarded.

   :<json str event_auth_token: The token to use for authentication if the event_url requires token auth.

   :<json dict channel_data: Additional data that is passed to the transport to interpret. E.g. ``continue_session`` for USSD, ``direct_message`` or ``tweet`` for Twitter.

   **Example request**:

   .. sourcecode:: json

      {
        "to": "+26612345678",
        "from": "8110",
        "reply_to": "uuid-1234",
        "event_url": "http://example.com/events/msg-1234",
        "content": "Hello world!",
        "channel_data": {
          "continue_session": true,
        }
      }

   **Example response**:

   .. sourcecode:: json

      {
        "status": 201,
        "code": "created",
        "description": "message submitted",
        "result": {
          "message_id": "message-uuid-5678"
        }
      }

Inbound message API
"""""""""""""""""""

Inbound messages that are ``POST``\ed to :py:data:`mo_message_url` have the following format:

.. http:post:: /<mo_message_url>

   :<json str to: The address that the message was sent to.

   :<json str from: The address that the message was sent from.

   :<json str group: If the transport supports groups, the group that the message was sent in.

   :<json str message_id: The string representation of the UUID of the message.

   :<json str channel_id: The name of the transport that the message came in on.

   :<json str timestamp: The timestamp of when the message arrived at the transport, in the format ``%Y-%m-%d %H:%M:%S.%f``.

   :<json str reply_to: If this message is a reply of an outbound message, the string representation of the UUID of the outbound message.

   :<json str content: The text content of the message.

   :<json dict channel_data: Any transport specific data. The contents of this differs between transport implementations.

**Request example**:

.. sourcecode:: json

    {
        "to": "+27821234567",
        "from": "12345",
        "group": null,
        "message_id": "35f3336d4a1a46c7b40cd172a41c510d"
        "channel_id": "bc5f2e63-7f53-4996-816d-4f89f45a5842",
        "timestamp": "2015-10-06 14:16:34.578820",
        "reply_to": null,
        "content": "Test message",
        "channel_data": {
            "session_event": "new"
        },
    }

Event API
"""""""""

Events ``POST``\ed to the ``event_url`` specified in :http:post:`<base_url_path>/messages` have the following format:

.. http:post:: /<event_url>

   :<json str event_type: The type of the event. See the list of event types below.

   :<json str message_id: The UUID of the message the event is for.

   :<json str channel_id: The name of the transport the event occurred for.

   :<json str timestamp: The timestamp at which the event occurred.

   :<json dict event_details: Details specific to the event type.

Events are posted to the message's ``event_url`` after the message is submitted to the provider, and when delivery reports are received. The default settings allow events to arrive for up to 24 hours; any further events will not be forwarded.

**Request example**:

.. sourcecode:: json

   {
     "event_type": "submitted",
     "message_id": "msg-uuid-1234",
     "channel_id": "channel-uuid-5678",
     "timestamp": "2015-06-15 13:00:00",
     "event_details": {
        "...detail specific to the event type..."
     }
   }

**Event types**

Sent when the message is submitted to the provider:

* ``submitted``: message successfully sent to the provider.
* ``rejected``: message rejected by the transport.

Sent later when (or if) delivery reports are received:

* ``delivery_succeeded``: provider confirmed that the message was delivered.
* ``delivery_failed``: provider declared that message delivery failed.
* ``delivery_pending``: provider is still attempting to deliver the message.


.. _junebug-state-caches:

Junebug state caches
^^^^^^^^^^^^^^^^^^^^

A Junebug state cache stores everything required for event delivery and reply messages.

In memory state cache
"""""""""""""""""""""

``vumi2.applications.junebug_message_api.junebug_state_cache.InMemorySmppCache``

This is a state cache implementation that stores the data in memory. Because of this, it is not suitable to share the data across multiple processes, and it will not survive process restarts.

It has the following configuration fields:

.. py:data:: timeout
   :type: int

   The maximum amount of time (in seconds) to keep inbound messages (for replies) and event delivery informnation. Defaults to 24 hours.

.. py:data:: store_event_info
   :type: bool

   If ``false``, event information isn't stored. This is useful in combination with :py:data:`default_event_url` if all events need to be sent to the same URL. Defaults to ``true``.
