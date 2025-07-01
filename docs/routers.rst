Routers
=======

Routers manage moving messages between transports and applications according to a
specified set of rules.

.. py:currentmodule:: vumi2.routers

.. _to-address-router:

To address router
-----------------

``vumi2.routers.ToAddressRouter``

A to address router routes inbound messages from one or many transports according to the
to address on the message. The address is matched against a list of patterns, and is
sent to the first application whose configured pattern is matched against.

A default application can also be configured to forward any messages that do not match
any of the patterns.

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
   :type: list[dict[str, str]]

   A list of dictionaries with `name` and `pattern` keys, where the name value is the application name to send the inbound message to, and the pattern value is the regular expression pattern to match against.

.. py:data:: message_cache_class
   :type: str

   The path to the class to use for caching messages. Defaults to `vumi2.message_caches.MemoryMessageCache`, a message cache that caches the messages in memory. This transport caches outbound messages in order to know where to route the events for those messages. See :ref:`memory-message-cache` for more information

.. py:data:: message_cache_config
   :type: dict

   The config for the specified message cache.

.. py:data:: default_app
   :type: str

   The default application name to forward any messages that do not match ay of the `to_address_mappings`. Defaults to ``None``.

For example:

.. code-block:: yaml

    transport_names:
        - ussd_transport
    to_address_mappings:
        - name: home_application
          pattern: "^\\*1234#$"
        - name: app2
          pattern: "^\\*1234\\*1#$"
    default_app: home_application
