SMPP transport
--------------
This transport is an `SMPP 3.4`_ client, mostly used for SMS.

.. _SMPP 3.4: https://support.nowsms.com/discus/messages/1/SMPP_v3_4_Issue1_2-24857.pdf


Configuration
^^^^^^^^^^^^^
The transport has the following configuration options:

All the configuration items from the base worker, which you can find at :ref:`base-worker-configuration`

.. py:currentmodule:: vumi2.transports.smpp

.. py:data:: transport_name
   :type: str

   The name of the AMQP queue to put inbound messages and events on, and to consume outbound messages from. Defaults to ``smpp``

.. py:data:: host
   :type: str

   The host for the SMPP server. Can be an IPv4 address, IPv6 address, or hostname. Defaults to ``localhost``

.. py:data:: port
   :type: int

   The port to connect to on the SMPP server. Defaults to ``2775``

.. py:data:: system_id
   :type: str

   The client identification for connecting to the SMPP server. This has a maximum length of 15, and must be ASCII. Defaults to ``smppclient1``

.. py:data:: password
   :type: str

   The password for the client to authenticate itself to the SMPP server. This has a maximum length of 8, and must be ASCII. Defaults to ``password``

.. py:data:: system_type
   :type: str

   Identifies the type of system to the SMPP server. Has a maximum length of 12 characters, and must be ASCII. Defaults to no system type.

.. py:data:: interface_version
   :type: int

   Identifies the version of the SMPP protocol used by the client to the SMPP server. ``0`` to ``33`` indicates version 3.3 or earlier, ``34`` indicates version 3.4. Defaults to ``34``

.. py:data:: address_range
   :type: str

   Identifies the address or range of addresses served by this client to the server. Maximum length of 40 characters, must be ASCII. Defaults to no address range.

.. py:data:: smpp_enquire_link_interval
   :type: int

   The amount of time, in seconds, between EnquireLink calls. These calls are to ensure that the communication channel between the client and server is still healthly. Defaults to 55 seconds.

.. py:data:: sequencer_class
   :type: str

   The python path to the class to use for sequencing. The sequencer is responsible for providing the sequence numbers for PDUs. These start at 1 and end at 0x7FFFFFFF, after which the sequencer should roll over to 1 again. The sequencer numbers are used to link responses from the ESME to the original request, so it's important that a sequence number isn't reused while we're still waiting for a response for it. Defaults to ``vumi2.transports.smpp.sequencers.InMemorySequencer``, which stores the current sequence position in memory. This is not suitable if you have multiple processes connecting to the same ESME, as the memory is not shared, so the sequencer will not be shared, leading to the same sequence number being used in different processes, which will create overlaps. See :ref:`sequencers` for a list of available sequencers

.. py:data:: sequencer_config
   :type: dict

   The config that :py:data:`sequencer_class` requires. See :ref:`sequencers` for what configuration is required for the sequencer classes.

.. py:data:: submit_sm_processor_class
   :type: str

   The python path to the class used for generating submit short message (outbound message) requests. This class is responsible for taking an outbound vumi message, and returning a list of PDUs that represents that message, that can be sent to the ESME if we want to send that outbound message. Defaults to ``vumi2.transports.smpp.processors.SubmitShortMessageProcessor``, which provides default short message processing that should be usable across a majority of ESMEs. See :ref:`submit-short-message-processors` for a list of submit short message processors that are available.

.. py:data:: submit_sm_processor_config
   :type: dict

   The config that :py:data:`submit_sm_processor_class` requires. See :ref:`submit-short-message-processors` for what configuration is required for the various short message processor classes.

.. py:data:: sm_processor_class
   :type: str

   The python path to the class used for handling extracting inbound messages from deliver short message requests. This class is responsible for taking the DeliverSM PDUs, and generating inbound Messages from them. It also handles combining a multipart SMS that has been split into multiple PDUs. Defaults to ``vumi2.transports.smpp.processors.ShortMessageProcessor``, which should be usable across a majority of SMSCs. See :ref:`short-message-processors` for a list of short messages processors.

.. py:data:: sm_processor_config
   :type: dict

   The config that :py:data:`sm_processor_class` requires. See :ref:`short-message-processors` for what configuration is required for the various short message processors.

.. py:data:: dr_processor_class
   :type: str

   The python path to the class used for handling and extracting delivery reports from deliver short message requests. This class is reposonsible for taking the DeliverSM PDUs, and generating delivery report Events, if the PDU is for a delivery report. Defaults to ``vumi2.transports.smpp.processors.DeliveryReportProcesser``, which provides delivery report processing that should be usable across a majority of SMSCs. See :ref:`delivery-report-processors` for a list of delivery report processors that are available

.. py:data:: dr_processor_config
   :type: dict

   The config that :py:data:`dr_processor_class` requires. See :ref:`delivery-report-processors` for what configuration is required for the various delivery report processor classes.

.. py:data:: smpp_cache_class
   :type: str

   The python path to the class used for the SMPP cache. This class is resposible for caching the parts of a multipart message, and for caching the SMPP message IDs for delivery reports. Defaults to ``vumi2.transports.smpp.smpp_cache.InMemorySmppCache``, which stores the data in memory. See :ref:`smpp-caches` for a list of SMPP caches.

.. py:data:: smpp_cache_config
   :type: dict

   The config that :py:data:`smpp_cache_class` requires. See :ref:`smpp-caches` for what configuration is required for the SMPP caches that are available.


How it works
^^^^^^^^^^^^
The client creates a new connection to the configured host and port. Because the transport is making a connection to the server, it does not support the server initiating the connection, nor the Outbind command.

Once connected, it sends a bind transceiver command, with the configured :py:data:`system_id`, :py:data:`password`, :py:data:`system_type`, :py:data:`interface_version`, and :py:data:`address_range`. It then waits for a bind transceiver response, after which it can start sending and receiving messages.

Once it has bound, it sends an enquire link request, at the interval specified by :py:data:`smpp_enquire_link_interval`, to ensure that the connection is still alive.

.. _sequencers:

Sequencers
^^^^^^^^^^
Sequencers are responsible for providing sequence numbers for PDUs. SMPP messages are sent asynchronously, so replies are not necessarily in the same order that the requests were sent in. These sequence numbers are used to match replies from the ESME to the requests that we send them, so it's important that each request that we're waiting on a reply for has a unique sequence number.

These numbers range between 1 and 0x7FFFFFFF.

In-memory sequencer
"""""""""""""""""""
``vumi2.transports.smpp.sequencers.InMemorySequencer``

This sequencer stores the current sequence position in memory. It is provided for simple single-process setups, as well as for easy testing. It has no external requirements.

It is not suitable for cases where the sequence number generator needs to be shared across processes, or if the sequence position needs to be persisited across process restarts.

When it reaches 0x7FFFFFFF, it rolls over back to 1, assuming that the lower sequence numbers have been responded to already.

It has no configuration, any configuration fields passed to it will be ignored.

.. _submit-short-message-processors:

Submit Short Message Processors
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The job of the submit short message processor is to take outbound vumi messages, and convert them into equivalent PDUs to be sent to the ESME, in order to send the outbound message.

Default submit short message processor
""""""""""""""""""""""""""""""""""""""
``vumi2.transports.smpp.processors.SubmitShortMessageProcessor``

This sequencer is designed to work with most EMSEs.

It has the following configuration fields:

.. warning::
    These fields will be changed, to something better, and then documentation can be improved showing all the choices for each field.

.. py:data:: data_coding
   :type: int

   What data encoding to use. This sets both the ``data_coding`` field on the PDU, as well as sets the encoding that we use for the message body. The following encodings are supported: SMSC default (GSM03.38), ASCII, Latin 1, JIS (ISO 2022 JP), Cyrllic (ISO-8859-5), Latin/Hebrew (ISO-8859-8), UCS2

.. py:data:: multipart_handling
   :type: str

   How to handle splitting messages. Defaults to ``short_message``, which does not allow long messages. Other options are: ``message_payload``, which puts the whole message in the message_payload parameter of the PDU, ``multipart_sar``, which splits the message, and puts the part details in the SAR fields of the PDU, and ``multipart_udh``, which splits the message and puts the part details as a header in front of each message part.

.. py:data:: service_type
   :type: str

   Defaults to none. ESME specific, what string to put in the ``service_type`` field of the PDU.

.. py:data:: source_addr_ton
   :type: int

   Defaults to unknown. The type of number for the source address (the address of the service).

.. py:data:: source_addr_npi
   :type: int

   Defaults to unknown. The numbering plan indicator for the source address (the address of the service)

.. py:data:: dest_addr_ton
   :type: int

   Defaults to unknown. The type of number for the destination address (the address of the user).

.. py:data:: dest_addr_npi
   :type: int

   Defaults to ISDN. The numbering plan indicator for the destination address (the address of the user)

.. py:data:: registered_delivery
   :type: dict

   The configuration for registered delivery. Takes the following fields:

   delivery_receipt (int)
      Defaults to no receipt requested. The SMSC delivery receipt to request
   sme_originated_acks (list[int])
      Defaults to none. Which SME originated acknowledgements to request
   intermediate_notification (bool)
      Defaults to False. Whether or not to request intermediate notifications


.. _delivery-report-processors:

Delivery Report Processors
^^^^^^^^^^^^^^^^^^^^^^^^^^
The job of a delivery report processor is to take DeliverSM PDUs, and if it looks like a delivery report, return an Event representing that delivery report.

Default delivery report processor
"""""""""""""""""""""""""""""""""
``vumi2.transports.smpp.processors.DeliveryReportProcesser``

This delivery report processor is designed to work with most SMSCs.

It has the following configuration fields:

.. py:data:: regex
   :type: str

   The regular expression to use to determine and extract the delivery report out of the message body. Defaults to a regular expression that should work for most SMSCs

.. py:data:: status_mapping
   :type: dict

   A mapping between the delivery report status, and ``pending``, ``delivered`` and ``failed``. Defaults to a default mapping that should work for most SMSCs.


.. _short-message-processors:

Short Message Processors
^^^^^^^^^^^^^^^^^^^^^^^^
The job of a short message processor is to take the DeliverSM PDUs, process them, and return the equivalent inbound Message. It also handles multipart messaging, by waiting for all the PDUs that make up a Message, and then returning a single Message once we have all the parts.

Default short message processor
"""""""""""""""""""""""""""""""
``vumi2.transports.smpp.processors.ShortMessageProcessor``

This short message processor is designed to work with most SMSCs.

It has the following configuration fields:

.. py:data:: data_coding_overrides
   :type: dict

   This field can be used to override any of the default codecs used to decode the message body, or provide a codec name for any of the unhandled data codings, eg. if you want to specify ``OCTET_UNSPECIFIED`` as ``ascii``. Defaults to no overrides.


.. _smpp-caches:

SMPP Caches
^^^^^^^^^^^
An SMPP cache caches state that we require for the SMPP transport. Currently it has two jobs:

1. It caches the parts of an inbound multipart message, so that when we have all of the parts, we can submit it as a single message
1. It caches the relation between the SMPP message ID, and the vumi Message ID, so that we can know what message a delivery report is for when we receive it.

In memory SMPP cache
""""""""""""""""""""
``vumi2.transports.smpp.smpp_cache.InMemorySmppCache``

This is an SMPP cache implementation that stores the data in memory. Because of this, it is not suitable to share the data across multiple processes, and it will not survive process restarts.

It has the following configuration fields:

.. py:data:: timeout
   :type: int

   The maximum amount of time to keep SMPP message IDs for received delivery reports. Defaults to 24 hours.


Still to do
^^^^^^^^^^^
The transport is not yet complete, the following things need to still be done

- Support receiver and transmitter binds, not just transceiver.
- Better config for processors
- Outbound messages: support USSD
- Sequence number generation is currently just in memory. We might want to have this configurable to store in a place like Redis, to be shared across processes.
