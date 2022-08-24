Message Stores
==============

A message store will store messages and events to be retrieved at a later stage. Currently it's only being used for the :ref:`to-address-router`, to store outbound messages to reference at a later stage, to route events.

.. _memory-message-store:

Memory Message Store
--------------------

``vumi2.message_stores.MemoryMessageStore``

This message store stores messages in memory, for a limited amount of time.

Because it's stored in memory, it's not suitable for sharing across different instances, eg. you cannot have multiple router instances to scale up, and use the memory message store.

Messages are only removed on store or fetch, so memory won't be cleared until the next message comes along.

Configuration
^^^^^^^^^^^^^
The following configuration options are available:

timeout: int
    The time, in seconds, to keep messages for. Defaults to 1 hour.