from warnings import warn

from async_amqp import AmqpProtocol  # type: ignore
from async_amqp.exceptions import ChannelClosed  # type: ignore


async def delete_amqp_queues(amqp_connection: AmqpProtocol, queues: set[str]) -> None:
    """
    Delete all the provided queues, emitting warnings if they're in use
    or not empty.
    """
    for queue in queues:
        try:
            async with amqp_connection.new_channel() as channel:
                await channel.queue_delete(queue, if_empty=True, if_unused=True)
        except ChannelClosed as e:
            warn(e.message, stacklevel=2)
            async with amqp_connection.new_channel() as channel:
                await channel.queue_delete(queue)
