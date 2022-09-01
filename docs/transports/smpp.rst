SMPP transport
--------------
This transport is an `SMPP 3.4`_ client, mostly used for SMS.

.. _SMPP 3.4: https://support.nowsms.com/discus/messages/1/SMPP_v3_4_Issue1_2-24857.pdf


Configuration
^^^^^^^^^^^^^
The transport has the following configuration options:

All the configuration items from the base worker, which you can find at :ref:`base-worker-configuration`

transport_name (str)
    The name of the AMQP queue to put inbound messages and events on, and to consume outbound messages from. Defaults to `smpp`
host (str)
    The host for the SMPP server. Can be an IPv4 address, IPv6 address, or hostname. Defaults to `localhost`
port (int)
    The port to connect to on the SMPP server. Defaults to `2775`
system_id (str)
    The client identification for connecting to the SMPP server. This has a maximum length of 15, and must be ASCII. Defaults to `smppclient1`
password (str)
    The password for the client to authenticate itself to the SMPP server. This has a maximum length of 8, and must be ASCII. Defaults to `password`
system_type (str)
    Identifies the type of system to the SMPP server. Has a maximum length of 12 characters, and must be ASCII. Defaults to no system type.
interface_version (int)
    Identifies the version of the SMPP protocol used by the client to the SMPP server. `0` to `33` indicates version 3.3 or earlier, `34` indicates version 3.4. Defaults to `34`
address_range (str)
    Identifies the address or range of addresses served by this client to the server. Maximum length of 40 characters, must be ASCII. Defaults to no address range.
smpp_enquire_link_interval (int)
    The amount of time, in seconds, between EnquireLink calls. These calls are to ensure that the communication channel between the client and server is still healthly. Defaults to 55 seconds.


How it works
^^^^^^^^^^^^
The client creates a new connection to the configured host and port. Because the transport is making a connection to the server, it does not support the server initiating the connection, nor the Outbind command.

Once connected, it sends a bind transceiver command, with the configured `system_id`, `password`, `system_type`, `interface_version`, and `address_range`. It then waits for a bind transceiver response, after which it can start sending and receiving messages.

Once it has bound, it sends an enquire link request, at the interval specified by `smpp_enquire_link_interval`, to ensure that the connection is still alive.


Still to do
^^^^^^^^^^^
The transport is not yet complete, the following things need to still be done

- Support receiver and transmitter binds, not just transceiver.
- Support sending outbound messages
- Support inbound SMPP commands for inbound messages and delivery reports
- Support all other SMPP inbound commands
- Timeout for binding
- Timeout for enquire link
- Sequence number generation is currently just in memory. We might want to have this configurable to store in a place like Redis, to be shared across processes.