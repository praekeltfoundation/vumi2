Turn Channels API (EXPERIMENTAL: DO NOT USE IN PRODUCTION)
----------------------------------------------------------

This application provides bidirectional messaging over HTTP, intended
for integration with `Turn's Channels API <https://whatsapp.turn.io/docs/api/channel_api>`_.

Currently this only supports text message types. A future iteration will support other types.

.. py:currentmodule:: vumi2.applications.turn_channels_api

Configuration
^^^^^^^^^^^^^

The application has the following configuration options:

.. py:data:: connector_name
   :type: str

   The name of the AMQP queue to publish outbound message on, and to consume inbound messages and events from. Required.

.. py:data:: http_bind
   :type: str

   The address to bind the HTTP server to. Required.

.. py:data:: vumi_api_path
   :type: str

   The base URL path for outbound message HTTP requests. Outbound message requests must be POSTed to ``<base_url_path>/messages``. Defaults to an empty string.

.. py:data:: turn_api_url
   :type: str

   The base URL path for requests to Turn. Defaults to an empty string.

.. py:data:: default_from_addr
   :type: str

   The default from address to use for outbound messages. Required.

.. py:data:: auth_token
   :type: str

   The authorization token to use for requests to Turn. Required.

.. py:data:: turn_hmac_secret
   :type: str

   The secret key that Turn uses to sign its requests to us. Required.

.. py:data:: request_timeout
   :type: float

   Maximum time allowed (in seconds) for outbound message request handling. Defaults to 240 seconds.

.. py:data:: mo_message_url_timeout
   :type: float

   Maximum time allowed (in seconds) for inbound message HTTP requests. Defaults to 10 seconds.

.. py:data:: event_url_timeout
   :type: float

   Maximum time allowed (in seconds) for event HTTP requests. Defaults to 10 seconds.

.. py:data:: transport_type
   :type: str

   The transport_type to use for non-reply outbound messages. Defaults to ``sms``.

.. py:data:: message_cache_class
   :type: str

   The message cache class to use for storing inbound messages. Defaults to ``MemoryMessageCache``.

.. py:data:: message_cache_config
   :type: dict

   The configuration for the message cache. Defaults to an empty dictionary.

.. py:data:: max_retries
   :type: int

   The maximum number of retries to attempt for outbound message requests. Defaults to 3.

.. py:data:: retry_delay_exponent
   :type: int

   The exponent to use for retry delays. Defaults to 2.

.. py:data:: retry_delay_base
   :type: int

   The base to use for retry delays. Defaults to 2.


How it works
^^^^^^^^^^^^

The application worker listens on HTTP for outbound messages from Turn and forwards them over AMQP to a router or transport. Inbound messages and events are forwarded to Turn over HTTP.

Outbound message API
""""""""""""""""""""
When Turn needs to submit a message to a user, it will send a POST request to the configured URL.

For more information see the `Turn Channels API documentation <https://whatsapp.turn.io/docs/api/channel_api#receiving-outbound-messages-from-your-channel>`_.

.. http:post:: <base_url_path>/messages

   Send an outbound (mobile terminated) message.

   :<json str to: The address (e.g. MSISDN) to send the message to.

   :<json str from: The address the message is from. May be ``null`` if :py:data:`default_from_addr` is configured.

   :<json str reply_to: The uuid of the message being replied to if this is a response to a previous message. 
    Important for session-based transports like USSD. Turn doesn't supply a reply to address, so we plan to infer it 
    based on the last inbound message. Optional.

   :<json dict turn: The Turn message to send. Contains the message content. Required.

   **Example request**:

   .. code-block:: json

      {
        "to": "+26612345678",
        "from": "8110",
        "turn": {"type": "text", "text": {"body": "Hello world!"}},
      }

**Example response**:

   .. code-block:: json

      {
        "messages": [{"id": "message-uuid-5678"}]
      }

Inbound message API
"""""""""""""""""""

Inbound messages that are ``POST``\ed to :py:data:`turn_api_url`/messages have the following format:

.. http:post:: /<turn_api_url>/messages

   :<json dict contact: Information about the contact who sent the message.
   :<json str contact.id: The Turn contact ID, which is an MSISDN.
   :<json dict contact.profile: The contact's profile information.
   :<json str contact.profile.name: The contact's name.

   :<json dict message: The message received from the user.
   :<json str message.type: The type of message. Currently only ``text`` is supported.
   :<json dict message.text: Required when message type is ``text``.
   :<json str message.text.body: The text content of the message.
   :<json str message.from: The user ID as an MSISDN. A Channel can respond to a user using this ID.
   :<json str message.id: The ID for the message that was received by the Channel.
   :<json int message.timestamp: Unix timestamp indicating when the message was received from the user.

**Example response**:

.. code-block:: json

    {
        "contact": {
            "id": "+26612345678",
            "profile": {
                "name": "John Doe"
            }
        },
        "message": {
            "type": "text",
            "text": {
                "body": "Hello world!"
            },
            "from": "+26612345678",
            "id": "message-uuid-5678",
            "timestamp": "1628345678"
        }
    }

Event API
"""""""""
Events ``POST``\ed to :py:data:`turn_api_url`/statuses have the following format:

.. http:post:: /<turn_api_url>/statuses

   :<json str user_message_id: The UUID of the message the event is for.

   :<json str timestamp: The timestamp at which the event occurred.

   :<json str status: The status of the event. One of: sent, delivered.

Events are posted to the message's ``event_url`` after the message is submitted to the provider, and when delivery reports are received. The default settings allow events to arrive for up to 24 hours; any further events will not be forwarded.

**Request example**:

.. code-block:: json

   {
     "user_message_id": "msg-uuid-1234",
     "timestamp": "2015-06-15 13:00:00",
     "status": "sent"
   }

**Event types**

Sent when the message is submitted to the provider:

* ``sent``: message successfully sent to the provider.

Sent later when (or if) delivery reports are received:

* ``delivered``: provider confirmed that the message was delivered.

In the case where the delivery fails, Turn does not currently accept a failed status, so we send a ``sent`` event.


.. _turn-state-caches:

Turn state caches
^^^^^^^^^^^^^^^^^

A in-memory cache that stores the last inbound message for each user. This is used to link outgoing messages to incoming messages, which is required for USSD flows.

In memory state cache
"""""""""""""""""""""

See `Message Caches <../message_caches.rst>`_ for more information.
