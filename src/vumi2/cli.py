import argparse
import sys
from typing import List, Type

from attrs import fields
from attrs import has as is_attrs

from vumi2.config import load_config
from vumi2.workers import BaseWorker, BaseWorkerConfig


def root_parser() -> argparse.ArgumentParser:
    """
    Parser for the main entrypoint for the cli
    """
    parser = argparse.ArgumentParser(description="Vumi Command Line Interface")
    return parser


def worker_subcommand(
    parser: argparse.ArgumentParser, worker_cls: Type[BaseWorker]
) -> argparse.ArgumentParser:
    """
    This is the worker subcommand, which runs a vumi worker.
    """
    command = parser.add_subparsers(required=True).add_parser(
        name="worker", description="Run a vumi worker", help="Run a vumi worker"
    )
    command.add_argument("worker_class", help="The worker class to run")
    worker_config_options(worker_cls.CONFIG_CLASS, command)
    return command


def build_main_parser(worker_cls=BaseWorker):
    """
    Build the main parser for the CLI

    worker_cls will add specific options for the worker class config
    """
    parser = root_parser()
    worker_subcommand(parser=parser, worker_cls=worker_cls)
    return parser


def _create_argument_key(prefix: str, name: str):
    """
    Takes config keys like amqp_hostname and converts them to command line
    friendly keys like amqp-hostname
    """
    name = name.lower().replace("_", "-")
    if prefix:
        prefix = prefix.lower().replace("_", "-")
        return f"{prefix}-{name}"
    return name


def worker_config_options(
    cls: Type[BaseWorkerConfig], parser: argparse.ArgumentParser, prefix=""
):
    """
    Adds the config options that are specific to the worker class
    """
    for field in fields(cls):
        # Check if nested
        if field.type and is_attrs(field.type):
            worker_config_options(
                field.type, parser, _create_argument_key(prefix, field.name)
            )
        else:
            parser.add_argument(
                f"--{_create_argument_key(prefix, field.name)}",
                default=field.default,
            )
    return parser


def class_from_string(class_name: str):
    """
    Given a string, return the class object.
    """
    parts = class_name.split(".")
    module = ".".join(parts[:-1])
    m = __import__(module)
    for comp in parts[1:]:
        m = getattr(m, comp)
    return m


def run_worker(worker_cls: BaseWorker, args: List[str]):
    """
    Runs the worker specified by the worker class
    """
    parser = build_main_parser(worker_cls=worker_cls)
    parsed_args = parser.parse_args(args=args)
    config = load_config(cls=worker_cls.CONFIG_CLASS, cli=parsed_args)
    # TODO: Run the worker
    return config


def main(args=sys.argv[1:]):
    parser = build_main_parser()

    # If we know the worker class, then skip directly to parsing for that class,
    # otherwise --help doesn't show all the worker options.
    if len(args) >= 2 and args[0] == "worker":
        try:
            worker_cls = class_from_string(class_name=args[1])
            return run_worker(worker_cls=worker_cls, args=args)
        except ValueError:
            # If the second argument is not a valid class, then display an error
            parser.parse_args(args=args)  # If the argument is --help
            parser.print_help()
            print(f"Invalid worker class: {args[1]}")
            exit(1)

    parser.parse_args(args=args)
