Turn Channels API (EXPERIMENTAL: DO NOT USE IN PRODUCTION)
-------------------

This application provides bidirectional messaging over HTTP, intended
for integration with `Turn's Channels API <https://whatsapp.turn.io/docs/api/channel_api>`_.

Currently this only supports text message types. A future iteration will support other types.

Configuration
^^^^^^^^^^^^^

How it works
^^^^^^^^^^^^

The application worker listens on HTTP for outbound messages from the external application and forwards them over AMQP to a router or transport. Inbound messages and events are forwarded to the external application over HTTP.

Outbound message API
""""""""""""""""""""

Inbound message API
"""""""""""""""""""

Event API
"""""""""