Configuration
=============

Configuration is handled in three layers, each layer overriding anything configured in
the previous layer. These layers are, in order of highest to lowest priority:

#. :ref:`command-line-options`
#. :ref:`environment-variables`
#. :ref:`configuration-file`


Base worker configuration
-------------------------
These are the configuration options that are common to all worker types.

An example of a yaml file configuration:

.. code-block:: yaml

   amqp:
      hostname: localhost
      port: 5672
      username: guest
      password: guest
      vhost: /
   worker_concurrency: 10
   sentry_dsn: https://key@sentry.io/12345

config
^^^^^^

amqp
   The AMQP configuration. See :ref:`amqp-config` for details.
amqp_url
   The AMQP URL to use. For example, ``amqp://user:pass@host/vhost``. If specified, it
   is used instead of the amqp configuration.
worker_concurrency
   The number of messages to prefetch and process in parallel. Defaults to 20.
sentry_dsn
   The Sentry DSN to use. If specified, errors will be logged to Sentry.

.. _amqp-config:

amqp
^^^^

The amqp configuration options are:

hostname
   The hostname of the AMQP server. Defaults to ``127.0.0.1``
port
   The port of the AMQP server. Defaults to ``5672``
username
   The username to use to authenticate to the AMQP server. Defaults to ``guest``
password
   The password to use to authenticate to the AMQP server. Defaults to ``guest``
vhost
   The virtual host to use for the AMQP server. Defaults to ``/``


.. _command-line-options:

Command line options
--------------------
The command line options are parsed first. The command line tool is ``vumi2``. It
currently only has one task, ``worker``, which runs a Vumi worker.


worker
^^^^^^
``usage: vumi2 worker {options} worker_class``

positional arguments:

worker_class
   The python import path of the worker class to run

options:

-h, --help
   Show this message and exit.

Any additional configuration options specific to the worker class can also be passed
through the command line. They are converted from snake case to kebab case. For example,
to configure a ``redis_url`` option for a worker type that needs access to a Redis
instance, you would use ``--redis-url redis://localhost``.

Nested configuration options are also supported, they are specified by a single option
separated by a ``-`` symbol. For example, to configure ``amqp.host``, you would use
the option ``--amqp-host``.


.. _environment-variables:

Environment variables
---------------------
Environment variables are the second priority, so will get overwritten by command line
arguments. They are in screaming camel case, for example ``AMQP_HOSTNAME``.

There is a special environment variable, ``VUMI_CONFIG_PREFIX``, which specifies a
prefix for all environment variables. It defaults to no prefix. For example, if
``VUMI_CONFIG_PREFIX`` is set to ``VUMI``, then the environment variable
``VUMI_AMQP_HOSTNAME`` will be used instead of ``AMQP_HOSTNAME``.

Nested configuration options are also supported, they are specified by a single variable
separated by a ``_`` symbol. For example, to configure ``amqp.host``, you would use
the option ``AMQP_HOST``.

.. _configuration-file:

Configuration file
------------------
The configuration file is the third priority, so will be overwritten by both environment
variables and command line arguments. It is in YAML format.

There is a special environment variable, ``VUMI_CONFIG_FILE``, which specifies the path
to the configuration file. It defaults to ``config.yaml``.

Configuration is specified in snake case, and can be nested using dictionaries. For
example, configuring the AMQP host and port:

.. code-block:: yaml

   amqp:
      host: localhost
      port: 5672





