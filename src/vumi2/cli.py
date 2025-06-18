import argparse
import logging
import sys
from collections.abc import Iterable

import trio
from attrs import Attribute

from vumi2.amqp import create_amqp_client
from vumi2.config import BaseConfig, get_config_class, load_config, walk_config_class
from vumi2.errors import InvalidWorkerClass
from vumi2.workers import BaseWorker


def root_parser() -> argparse.ArgumentParser:
    """
    Parser for the main entrypoint for the cli
    """
    parser = argparse.ArgumentParser(description="Vumi Command Line Interface")
    return parser


def worker_subcommand(
    parser: argparse.ArgumentParser, worker_cls: type[BaseWorker]
) -> argparse.ArgumentParser:
    """
    This is the worker subcommand, which runs a vumi worker.
    """
    command = parser.add_subparsers(required=True).add_parser(
        name="worker", description="Run a vumi worker", help="Run a vumi worker"
    )
    command.add_argument("worker_class", help="The worker class to run")
    worker_config_options(get_config_class(worker_cls), command)
    return command


def build_main_parser(worker_cls=BaseWorker):
    """
    Build the main parser for the CLI

    worker_cls will add specific options for the worker class config
    """
    parser = root_parser()
    worker_subcommand(parser=parser, worker_cls=worker_cls)
    return parser


def _create_argument_key(*parts: str):
    """
    Takes config keys like amqp_hostname and converts them to command line
    friendly keys like amqp-hostname
    """
    return "-".join(part.lower().replace("_", "-") for part in parts if part)


def worker_config_options(
    cls: type[BaseConfig], parser: argparse.ArgumentParser, prefix=""
):
    """
    Adds the config options that are specific to the worker class
    """

    def add_arg(field: Attribute, prefix: Iterable[str]):
        key = _create_argument_key(*prefix, field.name)
        parser.add_argument(f"--{key}")

    walk_config_class(cls, add_arg, prefix)

async def run_worker(
    worker_cls: type[BaseWorker], args: list[str], task_status=trio.TASK_STATUS_IGNORED
) -> BaseWorker:
    """
    Runs the worker specified by the worker class
    """
    parser = build_main_parser(worker_cls=worker_cls)
    parsed_args = parser.parse_args(args=args)
    config = load_config(cls=get_config_class(worker_cls), cli=parsed_args)
    logging.basicConfig(level=config.log_level)
    async with create_amqp_client(config) as amqp_connection:
        async with trio.open_nursery() as nursery:
            async with worker_cls(
                nursery=nursery, amqp_connection=amqp_connection, config=config
            ) as worker:
                await worker.setup()
                task_status.started(worker)
                await worker._closed.wait()
            return worker


def main(args=sys.argv[1:]):
    parser = build_main_parser()

    # If we know the worker class, then skip directly to parsing for that class,
    # otherwise --help doesn't show all the worker options.
    if len(args) >= 2 and args[0] == "worker":
        try:
            worker_cls = class_from_string(class_path=args[1])
        except InvalidWorkerClass as e:
            # If the second argument is not a valid class, then display an error
            parser.parse_args(args=args)  # If the argument is --help
            parser.print_help()
            print(str(e))
            exit(1)
        return trio.run(run_worker, worker_cls, args)

    parser.parse_args(args=args)
