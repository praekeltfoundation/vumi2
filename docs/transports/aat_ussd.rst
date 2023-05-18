AAT USSD transport
------------------
This is a transport for integrating into `AAT`_/`Vodacom messaging`_ USSD HTTP RPC API.

It is often used together with the :ref:`to-address-router`, in order for a single USSD code to be shared between applications.

.. note::
    Because this is an HTTP RPC transport, we need to respond to the inbound HTTP request with the content that we want to send back to the user. This means that the outbound reply message needs to go to the same transport that received the inbound request, so you cannot have multiple instances of this transport with the same `transport_name`.

.. _AAT: https://www.aat.co.za/always-active-mobile/ussd/
.. _Vodacom messaging: https://www.vodacommessaging.co.za/ussdapi.asp


Configuration
^^^^^^^^^^^^^
The transport has the following configuration options:

**Common to all vumi workers**

All the configuration items from the base worker (see :ref:`base-worker-configuration` for details) are available. However, :py:data:`http_bind` is required rather than optional.

**Common to all HTTP RPC transports**

.. py:currentmodule:: vumi2.transports.aat_ussd

.. py:data:: transport_name
   :type: str

   Defaults to ``http_rpc``. This is the base of the routing key/queue that the transport will listen on and publish messages to. For example, with the default, it will publish messages to ``http_rpc.inbound``, events to ``http_rpc.event``, and will listen for messages on ``http_rpc.outbound``.

.. py:data:: web_path
   :type: str

   Defaults to ``/http_rpc``. This is the path that the transport will listen on for inbound requests from the USSD gateway.

.. py:data:: request_timeout
   :type: int

   Defaults to 4 minutes. This is an integer value representing seconds. This is the amount of time to wait for a reply from the application before closing the incoming connection with a 504 Gateway Timeout response.

**AAT USSD transport specific**

.. py:data:: base_url
   :type: str

   Defaults to ``http://localhost``. This is the base URL of this transport. It is used to build a full URL for the callback URL that is sent to the USSD gateway. It is combined with the `web_path` configuration parameter to build the full URL.


How it works
^^^^^^^^^^^^
The USSD gateway makes an HTTP GET request to the transport, with the following query parameters:

.. http:get:: <base_url>

   :query msisdn: The MSISDN of the user in international format.

   :query provider: The name of the mobile network operator, eg. ``Vodacom``

   :query request: The input that the user submitted on their device. For the start of the session,    this will be the USSD code that the user dialled. For subsequent requests, this will be the free text response from the user.

We take this request, and put a message on the inbound queue, and keep the HTTP connection open. We then wait for a message to appear on the outbound, that is a reply to the inbound message, and use the reply to response to the request and close the connection. If we don't receive a reply within :py:data:`request_timeout`, then we close the connection with a 504 Gateway Timeout response.

The response is an XML document that contains the outbound message content, and sets it to expect a freetext response, unless the session event is ``close``, in which case it will end the USSD session.

While the gateway allows for a list of choices to be sent to the user, we only use the freetext option, and give the responsibility of rendering options, and handling user responses, to the application.

For freetext responses, we're expected to give a callback URL, which can also contain query parameters that will be sent back to us in the next request. We use the :py:data:`base_url` and :py:data:`web_path` configuration parameters to build the callback URL, and add a ``to_addr`` query parameter, which contains the initial message content, ie. the USSD code that the user dialled intially. This performs two functions: Firstly, it gives us the to address to set on every message, since we're only given that on the first HTTP request; and secondly, it allows us to differentiate between a new request, and a response to a previous request, since we're using the same URL for both.
