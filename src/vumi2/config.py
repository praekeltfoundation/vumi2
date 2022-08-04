import os
from typing import Any, Dict

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

    @classmethod
    def deserialise(cls, config: Dict[str, Any]) -> "BaseConfig":
        return structure(config, cls)


def _create_key(prefix: str, name: str) -> str:
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
            env[field.name] = load_config_from_environment(
                field.type, _create_key(prefix, field.name), source
            )
        else:
            env[field.name] = source.get(_create_key(prefix, field.name))
    return env
