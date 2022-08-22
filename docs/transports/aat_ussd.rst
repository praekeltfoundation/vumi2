AAT USSD transport
------------------
This is a transport for integrating into `AAT`_/`Vodacom messaging`_ USSD HTTP RPC API.

It is often used together with the :ref:`to-address-router`, in order for a single USSD code to be shared between applications.

.. _AAT: https://www.aat.co.za/always-active-mobile/ussd/
.. _Vodacom messaging: https://www.vodacommessaging.co.za/ussdapi.asp


Configuration
^^^^^^^^^^^^^
The transport has the following configuration options:

**Common to all vumi workers**

http_bind
    Required. This is where the HTTP server will bind. For example, `localhost:8000` to
    bind to port 8000 on localhost, or `0.0.0.0:80` to bind to port 80 on all
    interfaces, or `unix:/tmp/socket` to bind to a unix socket. See `the hypercorn documentation`_ for more details. Note that HTTPS is not handled, we recommend using something like
    `nginx`_ in front of the transport to handle HTTPS.
amqp
    Optional. Contains the following keys to connect to the AMQP broker: `hostname`, `port`, `username`, `password`, `vhost`. Defaults to `127.0.0.1` on port `5672` with `guest` as the username and password, and a vhost of `/`.
amqp_url
    Optional. A URL to connect to the AMQP broker. If this is set, it will override the `amqp` configuration. For example, `amqp://guest:guest@localhost:5672/%2F`.
worker_concurrency
    Optional. The number of worker tasks to run concurrently. Defaults to 20. This is
    the maximum amount of concurrent connections allowed.
sentry_dsn
    Optional. If set, errors will be reported to Sentry.

.. _the hypercorn documentation: https://pgjones.gitlab.io/hypercorn/how_to_guides/binds.html
.. _nginx: https://nginx.org/en/docs/

**Common to all HTTP RPC transports**

transport_name
    Defaults to `http_rpc`. This is the base of the routing key/queue that the transport
    will listen on and publish messages to. For example, with the default, it will
    publish messages to `http_rpc.inbound`, events to `http_rpc.event`, and will listen
    for messages on `http_rpc.outbound`.
web_path
    Defaults to `/http_rpc`. This is the path that the transport will listen on for
    inbound requests from the USSD gateway.
request_timeout
    Defaults to 4 minutes. This is an integer value representing seconds. This is the
    amount of time to wait for a reply from the application before closing the incoming
    connection with a 504 Gateway Timeout response.

**AAT USSD transport specific**

base_url
    Defaults to `http://localhost`. This is the base URL of this transport. It is used to build a full URL for the callback URL that is sent to the USSD gateway. It is combined with the `web_path` configuration parameter to build the full URL.


How it works
^^^^^^^^^^^^
The USSD gateway makes an HTTP GET request to the transport, with the following query parameters:

msisdn
    The MSISDN of the user in international format.
provider
    The name of the mobile network operator, eg. `Vodacom`
request
    The input that the user submitted on their device. For the start of the session,    this will be the USSD code that the user dialled. For subsequent requests, this will be the free text response from the user.

We take this request, and put a message on the inbound queue, and keep the HTTP connection open. We then wait for a message to appear on the outbound, that is a reply to the inbound message, and use the reply to response to the request and close the connection. If we don't receive a reply within `request_timeout`, then we close the connection with a 504 Gateway Timeout response.

The response is an XML document that contains the outbound message content, and sets it to expect a freetext response, unless the session event is `close`, in which case it will end the USSD session.

While the gateway allows for a list of choices to be sent to the user, we only use the freetext option, and give the responsibility of rendering options, and handling user responses, to the application.

For freetext responses, we're expected to give a callback URL, which can also contain query parameters that will be sent back to us in the next request. We use the `base_url` and `web_path` configuration parameters to build the callback URL, and add a `to_addr` query parameter, which contains the initial message content, ie. the USSD code that the user dialled intially. This performs two functions: Firstly, it gives us the to address to set on every message, since we're only given that on the first HTTP request; and secondly, it allows us to differentiate between a new request, and a response to a previous request, since we're using the same URL for both.