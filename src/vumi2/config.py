import os
from argparse import Namespace
from collections.abc import Callable, Iterable
from logging import getLogger
from pathlib import Path
from typing import (
    Any,
    Generic,
    Protocol,
    TypeVar,
    get_type_hints,
)

import yaml
from attrs import Attribute, AttrsInstance, Factory, define, fields
from attrs import has as is_attrs
from cattrs import Converter, transform_error

logger = getLogger(__name__)

_conv = Converter(prefer_attrib_converters=True)

CT = TypeVar("CT", bound=AttrsInstance)


class Configurable(Protocol, Generic[CT]):
    config: CT


def get_config_class(cls: Configurable[CT] | type[Configurable[CT]]) -> type[CT]:
    return get_type_hints(cls)["config"]


def structure(config: dict, cls: type[CT]) -> CT:
    try:
        _conv.copy(forbid_extra_keys=True).structure(config, cls)
    except Exception as e:
        for err in transform_error(e, cls.__name__):
            logger.warning(f"Config error: {err}")
    return _conv.structure(config, cls)


def structure_config(
    config: dict,
    obj: Configurable[CT] | type[Configurable[CT]],
) -> CT:
    return structure(config, get_config_class(obj))


@define
class AmqpConfig:
    hostname: str = "127.0.0.1"
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    vhost: str = "/"


@define
class BaseConfig:
    amqp: AmqpConfig = Factory(AmqpConfig)
    amqp_url: str = ""
    worker_concurrency: int = 20
    sentry_dsn: str | None = None
    http_bind: str | None = None
    log_level: str = "INFO"
    middlewares: list[dict] = Factory(list)


ConfigCallback = Callable[[Attribute, Iterable[str]], Any]


def walk_config_class(
    cls: type[AttrsInstance], fn: ConfigCallback, *prefix: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for field in fields(cls):
        if field.type and is_attrs(field.type):
            # For nested configs we always include the dict, even if it's empty.
            result[field.name] = walk_config_class(field.type, fn, *prefix, field.name)
        else:
            # For individual fields, we ignore any `None` values.
            if (value := fn(field, prefix)) is not None:
                result[field.name] = value
    return result


def _create_env_var_key(*parts: str) -> str:
    return "_".join(part.upper() for part in parts if part)


def load_config_from_environment(
    cls=BaseConfig, prefix="", source=os.environ
) -> dict[str, Any]:
    """
    Given the config class and a prefix, load the config from the source.

    Designed to load from environment variables, where it's a flat mapping, so nested
    fields have to be separated by underscore, eg. amqp.hostname -> AMQP_HOSTNAME

    Prefix is for if all environment variables have a prefix, eg. amqp.hostname
    with prefix vumi -> VUMI_AMQP_HOSTNAME

    Returns a dictionary of the config, so that it can be merged with other config
    sources.
    """

    def load_envvar(field: Attribute, prefix: Iterable[str]) -> Any:
        key = _create_env_var_key(*prefix, field.name)
        return source.get(key, None)

    return walk_config_class(cls, load_envvar, prefix)


def _create_cli_key(*parts: str) -> str:
    return "_".join(part for part in parts if part)


def load_config_from_cli(
    source: Namespace, cls=BaseConfig, prefix=""
) -> dict[str, Any]:
    """
    Given the parsed command line arguments, the config class, and a prefix, load the
    worker config

    Returns a dictionary of the config, so that it can be merged with other config
    sources.
    """

    def load_cli(field: Attribute, prefix: Iterable[str]) -> Any:
        key = _create_cli_key(*prefix, field.name)
        return getattr(source, key, None)

    return walk_config_class(cls, load_cli, prefix)


def _combine_nested_dictionaries(*args: dict[Any, Any]):
    """
    Takes multiple dictionaries and combines them into a single dictionary, taking into
    account nested dictionaries.
    """
    result: dict[Any, Any] = {}
    for d in args:
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = _combine_nested_dictionaries(result.get(k, {}), v)
            else:
                result[k] = v
    return result


def load_config_from_file(path: Path | None) -> dict[str, Any]:
    """
    Load config information from a file if one is specified, otherwise
    return an empty dict.
    """
    if path is None:
        return {}

    with path.open() as f:
        return yaml.safe_load(f)


def load_config(cls=BaseConfig, cli=None) -> BaseConfig:
    """
    Load the entire config from all sources.

    Priority from least to most is:
    - Configuration file specified by environment variable VUMI_CONFIG_FILE, defaulting
      to config.yaml
    - Environment variables, with prefix specified by VUMI_CONFIG_PREFIX, defaulting to
      no prefix
    - Command line arguments
    """
    cli = Namespace() if cli is None else cli
    config_path_envvar = os.environ.get("VUMI_CONFIG_FILE", "")
    config_path = Path(config_path_envvar) if config_path_envvar else None
    config_prefix = os.environ.get("VUMI_CONFIG_PREFIX", "")
    config_env = load_config_from_environment(
        cls=cls, prefix=config_prefix, source=os.environ
    )
    config_file = load_config_from_file(path=config_path)
    config_cli = load_config_from_cli(source=cli, cls=cls)
    config_dict = _combine_nested_dictionaries(config_file, config_env, config_cli)
    return structure(config_dict, cls)
