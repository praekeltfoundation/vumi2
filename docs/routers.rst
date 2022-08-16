Routers
=======

Routers manage moving messages between transports and applications according to a
specified set of rules.

To address router
-----------------

``vumi2.routers.ToAddressRouter``

A to address router routes inbound messages from one or many transports according to the
to address on the message. The address is matched against a list of patterns, and is
sent to any applications whose configured pattern is matched against.

Outbound messages from applications are routed to the transport that the are in reply
to. The ``transport_name`` field on the message is used to determine which transport
to send the message to.

.. warning::
    Transport events are currently not handled by the router, and are discarded.

A good use for this router is on USSD, where for example you have a transport for the
``*1234#`` USSD code. Then you can route the base code ``*1234#`` to one application,
and the ``*1234*1#`` to another.

Configuration
^^^^^^^^^^^^^
The following configuration options are available. See :ref:`base-worker-configuration`
for the additional configuration options available for all workers.

transport_names: list[str]
    The names of the transports that we're consuming inbound messages from, and routing
    outbound messages to.
to_address_mappings: dict[str, str]
    The keys of this dictionary are the application names to send the inbound messages
    to, and the values are the regular expression patterns to match against

For example:

.. code-block:: yaml

    transport_names:
        - ussd_transport
    to_address_mappings:
        home_application: "^\\*1234#$"
        app2: "^\\*1234\\*1#$"