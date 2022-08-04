from typing import Any, Dict

from attrs import Factory, define
from cattrs import structure


@define
class AmqpConfig:
    hostname: str = "127.0.0.1"
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    vhost: str = "/"


@define(slots=False)
class BaseConfig:
    amqp: AmqpConfig = Factory(AmqpConfig)
    amqp_url: str = ""
    worker_concurrency: int = 20

    @classmethod
    def deserialise(cls, config: Dict[str, Any]) -> "BaseConfig":
        return structure(config, cls)
