Routers
=======

Routers manage moving messages between transports and applications according to a
specified set of rules.

.. _to-address-router:

To address router
-----------------

``vumi2.routers.ToAddressRouter``

A to address router routes inbound messages from one or many transports according to the
to address on the message. The address is matched against a list of patterns, and is
sent to any applications whose configured pattern is matched against.

Outbound messages from applications are routed to the transport that the are in reply
to. The ``transport_name`` field on the message is used to determine which transport
to send the message to.

A good use for this router is on USSD, where for example you have a transport for the
``*1234#`` USSD code. Then you can route the base code ``*1234#`` to one application,
and the ``*1234*1#`` to another.

Configuration
^^^^^^^^^^^^^
The following configuration options are available. See :ref:`base-worker-configuration`
for the additional configuration options available for all workers.

.. py:data:: transport_names
   :type: list[str]

   The names of the transports that we're consuming inbound messages from, and routing outbound messages to.

.. py:data:: to_address_mappings
   :type: dict[str, str]

   The keys of this dictionary are the application names to send the inbound messages to, and the values are the regular expression patterns to match against

.. py:data:: message_cache_class
   :type: str

   The path to the class to use for caching messages. Defaults to `vumi2.message_caches.MemoryMessageCache`, a message cache that caches the messages in memory. This transport caches outbound messages in order to know where to route the events for those messages. See :ref:`memory-message-cache` for more information

.. py:data:: message_cache_config
   :type: dict

   The config for the specified message cache.

For example:

.. code-block:: yaml

    transport_names:
        - ussd_transport
    to_address_mappings:
        home_application: "^\\*1234#$"
        app2: "^\\*1234\\*1#$"
