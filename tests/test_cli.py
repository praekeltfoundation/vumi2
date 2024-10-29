import contextlib
import io
from argparse import ArgumentParser
from pathlib import Path
from textwrap import dedent

import pytest
from trio import fail_after

from vumi2.cli import (
    build_main_parser,
    class_from_string,
    main,
    root_parser,
    run_worker,
    worker_config_options,
    worker_subcommand,
)
from vumi2.config import BaseConfig
from vumi2.workers import BaseWorker


def test_root_parser():
    parser = root_parser()
    assert parser.description == "Vumi Command Line Interface"


def test_worker_subcommand():
    parser = root_parser()
    subcommand = worker_subcommand(parser=parser, worker_cls=BaseWorker)
    assert subcommand.description == "Run a vumi worker"


def test_build_main_parser():
    parser = build_main_parser()
    assert isinstance(parser, ArgumentParser)
    assert parser.description == "Vumi Command Line Interface"

    args = parser.parse_args(
        ["worker", "vumi2.workers.BaseWorker", "--amqp-hostname", "localhost"]
    )
    assert args.amqp_hostname == "localhost"


def test_worker_config_options():
    parser = ArgumentParser()
    worker_config_options(cls=BaseConfig, parser=parser)
    args = parser.parse_args(["--amqp-hostname", "localhost"])
    assert args.amqp_hostname == "localhost"


def test_class_from_string():
    assert class_from_string("vumi2.workers.BaseWorker") == BaseWorker


async def test_run_worker(nursery):
    args = ["worker", "vumi2.workers.BaseWorker", "--amqp-hostname", "localhost"]

    with fail_after(2):
        worker = await nursery.start(run_worker, BaseWorker, args)

        assert worker.config.amqp.hostname == "localhost"
        # The worker's still running, because we haven't stopped it yet.
        assert not worker._closed.is_set()

        await worker.aclose()
        # When we're done, the worker should be closed.
        assert worker._closed.is_set()


def _get_main_command_output(args: list[str]) -> str:
    err = None
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(new_target=output):
            main(args)
    except SystemExit as e:
        err = e
    assert err is not None
    return output.getvalue()


def test_main_print_help():
    output = _get_main_command_output(["--help"])

    help_text = io.StringIO()
    parser = build_main_parser()
    parser.print_help(help_text)

    assert output == help_text.getvalue()


# TODO: Improve all the above tests to differentiate by behaviour rather than
# function and to have proper docstrings, etc.


def test_worker_without_module_and_class():
    """
    A valid worker class is a fully qualified Python name for a class. This
    means we need both a module part and a class part for a name to be valid.
    """

    # TODO: Better output for this?
    output = _get_main_command_output(["worker", "module_only"])
    assert "Error loading 'module_only': Invalid worker class" in output

    output = _get_main_command_output(["worker", "ClassOnly"])
    assert "Error loading 'ClassOnly': Invalid worker class" in output


def _write_python_module(path: Path, body: str):
    # Remove leading and trailing newlines to account for triple-quotes on
    # their own lines, then use textwrap.dedent() to remove all shared
    # indentation.
    body = dedent(body.strip("\n"))
    path.write_text(body)


def test_worker_module_missing(monkeypatch, tmp_path):
    """
    When we try to load a worker class from a module that doesn't exist, we
    get a suitable error.
    """

    output = _get_main_command_output(["worker", "nothing_to_see_here.Worker"])
    assert "Error loading 'nothing_to_see_here.Worker': " in output
    assert ": No module named 'nothing_to_see_here'" in output

    output = _get_main_command_output(["worker", "ntsh.foo.bar.Worker"])
    assert "Error loading 'ntsh.foo.bar.Worker': " in output
    assert ": No module named 'ntsh'" in output

    # Getting a prefix that isn't just the first module requires us to have the
    # first module available.
    _write_python_module(tmp_path / "mod_top.py", "")

    monkeypatch.syspath_prepend(tmp_path)

    output = _get_main_command_output(["worker", "mod_top.blah.Worker"])
    assert "Error loading 'mod_top.blah.Worker': " in output
    assert ": No module named 'mod_top.blah'" in output


def test_worker_class_missing(monkeypatch, tmp_path):
    """
    When we try to load a worker class from a module that exist but doesn't
    contain the worker class, we get a suitable error.
    """
    _write_python_module(tmp_path / "mod_noclass.py", "")

    monkeypatch.syspath_prepend(tmp_path)

    output = _get_main_command_output(["worker", "mod_noclass.Worker"])
    assert "Error loading 'mod_noclass.Worker': " in output
    assert ": No class named 'Worker'" in output


def test_worker_module_with_import_error(monkeypatch, tmp_path):
    """
    When we try to load a worker class from a module that exists but has
    its own import error, that import error is raised as-is instead of being
    reported as a missing worker class.
    """

    _write_python_module(
        tmp_path / "mod_import_err.py",
        """
        import something_that_does_not_exist
        """,
    )

    monkeypatch.syspath_prepend(tmp_path)

    with pytest.raises(ModuleNotFoundError) as e_info:
        _get_main_command_output(["worker", "mod_import_err.Class"])
    assert e_info.value.name == "something_that_does_not_exist"


def test_worker_module_with_import_error_substring(monkeypatch, tmp_path):
    """
    When we try to load a worker class from a module that exists but has
    its own import error, that import error is raised as-is even if the module
    being imported is a string prefix (but not a module prefix) of the module
    we're trying to import.
    """

    # In this case, the parent module is trying to import something that's a
    # string prefix of out module path, but not a path prefix. It's still a bug
    # in the worker module, so we need to make sure we detect it as one.
    _write_python_module(
        tmp_path / "mod_import_err2.py",
        """
        import mod_import_err2.bl
        """,
    )

    monkeypatch.syspath_prepend(tmp_path)

    with pytest.raises(ModuleNotFoundError) as e_info:
        _get_main_command_output(["worker", "mod_import_err2.blah.Class"])
    assert e_info.value.name == "mod_import_err2.bl"


def test_worker_module_with_other_error(monkeypatch, tmp_path):
    """
    When we try to load a worker class from a module that raises some
    arbitrary exception on import, that exception isn't caught.
    """

    _write_python_module(
        tmp_path / "mod_assert_err.py",
        """
        assert False, "oops"
        """,
    )

    monkeypatch.syspath_prepend(tmp_path)

    with pytest.raises(AssertionError) as e_info:
        _get_main_command_output(["worker", "mod_assert_err.Class"])
    assert e_info.value.args[0] == "oops"
