import contextlib
import io
from argparse import ArgumentParser
from typing import List

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


async def test_run_worker():
    worker = await run_worker(
        worker_cls=BaseWorker,
        args=[
            "worker",
            "vumi2.workers.BaseWorker",
            "--amqp-hostname",
            "localhost",
        ],
    )
    assert worker.config.amqp.hostname == "localhost"


def _get_main_command_output(args: List[str]) -> str:
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


def test_main_invalid_worker_class():
    output = _get_main_command_output(["worker", "invalid"])
    assert "Invalid worker class" in output


def test_main_valid():
    worker = main(
        args=["worker", "vumi2.workers.BaseWorker", "--amqp-url", "amqp://localhost"],
    )
    assert worker.config.amqp_url == "amqp://localhost"
