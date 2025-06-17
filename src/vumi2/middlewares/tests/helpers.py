import json
import os
from functools import wraps
from inspect import CO_GENERATOR
from itertools import dropwhile
import traceback

from twisted.internet.defer import succeed, inlineCallbacks, Deferred
from twisted.internet.error import ConnectionRefusedError
from twisted.internet.task import deferLater
from twisted.python.failure import Failure
from twisted.python.monkey import MonkeyPatcher
from twisted.trial.unittest import TestCase, SkipTest, FailTest

from zope.interface import Interface, implements

from contextlib import asynccontextmanager
from typing import TypeVar
from warnings import warn

import pytest
from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.exceptions import ChannelClosed  # type: ignore
from attrs import define, field
from trio import MemoryReceiveChannel, Nursery, fail_after, open_memory_channel
from trio.abc import AsyncResource

from vumi2.amqp import create_amqp_client
from vumi2.config import load_config, structure_config
from vumi2.connectors import (
    ConnectorCollection,
    Consumer,
    ReceiveInboundConnector,
    ReceiveOutboundConnector,
)
from vumi2.messages import Event, Message
from vumi2.workers import BaseWorker



class IHelper(Interface):
    """
    Interface for test helpers.

    This specifies a standard setup and cleanup mechanism used by test cases
    that implement the :class:`IHelperEnabledTestCase` interface.

    There are no interface restrictions on the constructor of a helper.
    """

    def setup(*args, **kwargs):
        """
        Perform potentially async helper setup.

        This may return a deferred for async setup or block for sync setup. All
        helpers must implement this even if it does nothing.

        If the setup is optional but commonly used, this method can take flags
        to perform or suppress all or part of it as required.
        """

    def cleanup():
        """
        Clean up any resources created by this helper.

        This may return a deferred for async cleanup or block for sync cleanup.
        All helpers must implement this even if it does nothing.
        """


def get_timeout():
    """
    Look up the test timeout in the ``VUMI_TEST_TIMEOUT`` environment variable.

    A default of 5 seconds is used if there isn't one there.
    """
    timeout_str = os.environ.get('VUMI_TEST_TIMEOUT', '5')
    return float(timeout_str)

class IHelperEnabledTestCase(Interface):
    """
    Interface for test cases that use helpers.

    This specifies a standard mechanism for managing setup and cleanup of
    helper classes that implement the :class:`IHelper` interface.
    """

    def add_helper(helper_object, *args, **kwargs):
        """
        Register cleanup and perform setup for a helper object.

        This should call ``helper_object.setup(*args, **kwargs)`` and
        ``self.add_cleanup(helper_object.cleanup)`` or an equivalent.

        Returns the ``helper_object`` passed in or a :class:`Deferred` if
        setup is async.
        """

class VumiTestCase(TestCase):
    """
    Base test case class for all things vumi-related.

    This is a subclass of :class:`twisted.trial.unittest.TestCase` with a small
    number of additional features:

    * It implements :class:`IHelperEnabledTestCase` to make using helpers
      easier. (See :meth:`add_helper`.)

    * :attr:`timeout` is set to a default value of ``5`` and can be overridden
      by setting the ``VUMI_TEST_TIMEOUT`` environment variable. (Longer
      timeouts are more reliable for continuous integration builds, shorter
      ones are less painful for local development.)

    * :meth:`add_cleanup` provides an alternative mechanism for specifying
      cleanup in the same place as the creation of thing that needs to be
      cleaned up.

    .. note::

       While this class does not have a :meth:`setUp` method (thus avoiding the
       need for subclasses to call it), it *does* have a :meth:`tearDown`
       method. :meth:`add_cleanup` should be used in subclasses instead of
       overriding :meth:`tearDown`.
    """

    implements(IHelperEnabledTestCase)

    timeout = get_timeout()
    reactor_check_interval = 0.01  # 10ms, no science behind this number.
    reactor_check_iterations = 100  # No science behind this number either.

    _cleanup_funcs = None

    @inlineCallbacks
    def tearDown(self):
        """
        Run any cleanup functions registered with :meth:`add_cleanup`.
        """
        # Run any cleanup code we've registered with .add_cleanup().
        # We do this ourselves instead of using trial's .addCleanup() because
        # that doesn't have timeouts applied to it.
        if self._cleanup_funcs is not None:
            for cleanup, args, kw in reversed(self._cleanup_funcs):
                yield cleanup(*args, **kw)
        yield self._check_reactor_things()

    @inlineCallbacks
    def _check_reactor_things(self):
        """
        Poll the reactor for unclosed connections and wait for them to close.

        Properly waiting for all connections to finish closing requires hooking
        into :meth:`Protocol.connectionLost` in both client and server. Since
        this isn't practical in all cases, we check the reactor for any open
        connections and wait a bit for them to finish closing if we find any.

        NOTE: This will only wait for connections that close on their own. Any
              connections that have been left open will stay open (unless they
              time out or something) and will leave the reactor dirty after we
              stop waiting.
        """
        from twisted.internet import reactor
        # Give the reactor a chance to get clean.
        yield deferLater(reactor, 0, lambda: None)

        for i in range(self.reactor_check_iterations):
            # There are some internal readers that we want to ignore.
            # Unfortunately they're private.
            internal_readers = getattr(reactor, '_internalReaders', set())
            selectables = set(reactor.getReaders() + reactor.getWriters())
            if not (selectables - internal_readers):
                # The reactor's clean, let's go home.
                return

            # We haven't gone home, so wait a bit for selectables to go away.
            yield deferLater(
                reactor, self.reactor_check_interval, lambda: None)

    def add_cleanup(self, func, *args, **kw):
        """
        Register a cleanup function to be called at teardown time.

        :param callable func:
            The callable object to call at cleanup time. This callable may
            return a :class:`Deferred`, in which case cleanup will continue
            after it fires.
        :param \*args: Passed to ``func`` when it is called.
        :param \**kw: Passed to ``func`` when it is called.

        .. note::
           This method should be use in place of the inherited
           :meth:`addCleanup` method, because the latter doesn't apply timeouts
           to cleanup functions.
        """
        if self._cleanup_funcs is None:
            self._cleanup_funcs = []
        self._cleanup_funcs.append((func, args, kw))

    def add_helper(self, helper_object, *args, **kw):
        """
        Perform setup and register cleanup for the given helper object.

        :param helper_object:
            Helper object to add. ``helper_object`` must provide the
            :class:`IHelper` interface.
        :param \*args: Passed to :meth:`helper_object.setup` when it is called.
        :param \**kw: Passed to :meth:`helper_object.setup` when it is called.

        :returns:
            Either ``helper_object`` or a :class:`Deferred` that fires with it.

        If :meth:`helper_object.setup` returns a :class:`Deferred`, this method
        also returns a :class:`Deferred`.

        Example usage assuming ``@inlineCallbacks``:

        >>> @inlineCallbacks
        ... def test_foo(self):
        ...     msg_helper = yield self.add_helper(MessageHelper())
        ...     msg_helper.make_inbound("foo")

        Example usage assuming non-async setup:

        >>> def test_bar(self):
        ...     msg_helper = self.add_helper(MessageHelper())
        ...     msg_helper.make_inbound("bar")

        """

        if not IHelper.providedBy(helper_object):
            raise ValueError(
                "Helper object does not provide the IHelper interface: %s" % (
                    helper_object,))
        self.add_cleanup(helper_object.cleanup)
        return maybe_async_return(
            helper_object, helper_object.setup(*args, **kw))

    def _runFixturesAndTest(self, result):
        """
        Override trial's ``_runFixturesAndTest()`` method to detect test
        methods that are generator functions, indicating a missing
        ``@inlineCallbacks`` decorator.

        NOTE: This should probably be removed when
              https://twistedmatrix.com/trac/ticket/3917 is merged and the next
              Twisted version (probably 14.0) is released.
        """
        method = getattr(self, self._testMethodName)
        if method.func_code.co_flags & CO_GENERATOR:
            # We have a generator that isn't wrapped in @inlineCallbacks
            e = ValueError(
                "Test method is a generator. Missing @inlineCallbacks?")
            result.addError(self, Failure(e))
            return
        return super(VumiTestCase, self)._runFixturesAndTest(result)

def maybe_async_return(value, maybe_deferred):
    """
    Return ``value`` or a deferred that fires with it.

    This is useful in cases where we're performing a potentially async
    operation but don't necessarily have enough information to use
    `maybe_async`.
    """
    if isinstance(maybe_deferred, Deferred):
        return maybe_deferred.addCallback(lambda r: value)
    return value