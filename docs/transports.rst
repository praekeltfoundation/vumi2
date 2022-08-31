Transports
==========
Transports communicate with external services, taking requests from those services and
converting them to Vumi messages, and taking the application replies, in the format
of Vumi messages, and relaying them back to the external service.

It is both a translation layer between the vumi message format and the service specific
format, and a transport layer to communicate those messages and events back and forth
from the service.

The following transports are available:

.. toctree::
    :maxdepth: 2

    transports/aat_ussd
    transports/smpp