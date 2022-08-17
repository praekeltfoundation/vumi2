import os
from argparse import Namespace
from typing import Any, Dict, Optional

import yaml
from attrs import Factory, define, fields
from attrs import has as is_attrs
from cattrs import structure


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
    sentry_dsn: Optional[str] = None
    http_bind: Optional[str] = None

    @classmethod
    def deserialise(cls, config: Dict[str, Any]) -> "BaseConfig":
        return structure(config, cls)


def _create_env_var_key(prefix: str, name: str) -> str:
    if prefix:
        return f"{prefix.upper()}_{name.upper()}"
    return name.upper()


def load_config_from_environment(
    cls=BaseConfig, prefix="", source=os.environ
) -> Dict[str, Any]:
    """
    Given the config class and a prefix, load the config from the source.

    Designed to load from environment variables, where it's a flat mapping, so nested
    fields have to be separated by underscore, eg. amqp.hostname -> AMQP_HOSTNAME

    Prefix is for if all environment variables have a prefix, eg. amqp.hostname
    with prefix vumi -> VUMI_AMQP_HOSTNAME

    Returns a dictionary of the config, so that it can be merged with other config
    sources.
    """
    env: Dict[str, Any] = {}

    for field in fields(cls):
        # Check if nested
        if field.type and is_attrs(field.type):
            value = load_config_from_environment(
                field.type, _create_env_var_key(prefix, field.name), source
            )
            if value:
                env[field.name] = value
        else:
            key = _create_env_var_key(prefix, field.name)
            if key in source:
                env[field.name] = source[key]
    return env


def _create_cli_key(prefix: str, name: str) -> str:
    if prefix:
        return f"{prefix}_{name}"
    return name


def load_config_from_cli(
    source: Namespace, cls=BaseConfig, prefix=""
) -> Dict[str, Any]:
    """
    Given the parsed command line arguments, the config class, and a prefix, load the
    worker config

    Returns a dictionary of the config, so that it can be merged with other config
    sources.
    """
    conf: Dict[str, Any] = {}

    for field in fields(cls):
        # Check if nested
        if field.type and is_attrs(field.type):
            value = load_config_from_cli(
                source,
                field.type,
                _create_cli_key(prefix, field.name),
            )
            if value:
                conf[field.name] = value
        else:
            key = _create_cli_key(prefix, field.name)
            if hasattr(source, key) and getattr(source, key) is not None:
                conf[field.name] = getattr(source, key)
    return conf


def _combine_nested_dictionaries(*args: Dict[Any, Any]):
    """
    Takes multiple dictionaries and combines them into a single dictionary, taking into
    account nested dictionaries.
    """
    result: Dict[Any, Any] = {}
    for d in args:
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = _combine_nested_dictionaries(result.get(k, {}), v)
            else:
                result[k] = v
    return result


def load_config_from_file(filename: str) -> Dict[str, Any]:
    """
    Loads a config from a yaml file, if it exists, otherwise returns an empty
    dictionary.
    """
    if os.path.exists(filename):
        with open(filename) as f:
            return yaml.safe_load(f)
    return {}


def load_config(cls=BaseConfig, cli=Namespace()) -> BaseConfig:
    """
    Load the entire config from all sources.

    Priority from least to most is:
    - Configuration file specified by environment variable VUMI_CONFIG_FILE, defaulting
      to config.yaml
    - Environment variables, with prefix specified by VUMI_CONFIG_PREFIX, defaulting to
      no prefix
    - Command line arguments
    """
    config_filename = os.environ.get("VUMI_CONFIG_FILE", "config.yaml")
    config_prefix = os.environ.get("VUMI_CONFIG_PREFIX", "")
    config_env = load_config_from_environment(
        cls=cls, prefix=config_prefix, source=os.environ
    )
    config_file = load_config_from_file(filename=config_filename)
    config_cli = load_config_from_cli(source=cli, cls=cls)
    return cls.deserialise(
        _combine_nested_dictionaries(config_file, config_env, config_cli)
    )
