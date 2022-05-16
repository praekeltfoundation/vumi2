from datetime import datetime
from uuid import UUID

import pytest

from vumi2.messages import (
    AddressType,
    Message,
    Session,
    TransportType,
    deserialise_vumi_timestamp,
    generate_message_id,
    serialise_vumi_timestamp,
)


def test_deserialise_all_fields():
    """
    Test that deserialisation works when all fields are specified
    """
    data = {
        "to_addr": "+27820001001",
        "from_addr": "12345",
        "transport_name": "test_sms",
        "transport_type": "sms",
        "message_version": "20110921",
        "message_type": "user_message",
        "timestamp": "2022-05-16 13:14:15.123456",
        "routing_metadata": {"test": "routing"},
        "helper_metadata": {"test": "helper"},
        "message_id": "7c9af210bc1b4718ae74a1aa026b4757",
        "in_reply_to": "c333975405074a89bf5d686ac64cc4d3",
        "provider": "Vodacom",
        "session_event": "new",
        "content": "test message",
        "transport_metadata": {"test": "transport"},
        "group": "test_group",
        "to_addr_type": "msisdn",
        "from_addr_type": "msisdn",
    }
    assert Message.deserialise(data) == Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="test_sms",
        transport_type=TransportType.SMS,
        message_version="20110921",
        message_type="user_message",
        timestamp=datetime(2022, 5, 16, 13, 14, 15, 123456),
        routing_metadata={"test": "routing"},
        helper_metadata={"test": "helper"},
        message_id="7c9af210bc1b4718ae74a1aa026b4757",
        in_reply_to="c333975405074a89bf5d686ac64cc4d3",
        provider="Vodacom",
        session_event=Session.NEW,
        content="test message",
        transport_metadata={"test": "transport"},
        group="test_group",
        to_addr_type=AddressType.MSISDN,
        from_addr_type=AddressType.MSISDN,
    )


def test_serialise_all_fields():
    """
    Serialisation works when all fields are specified
    """
    msg = Message(
        to_addr="+27820001001",
        from_addr="12345",
        transport_name="test_sms",
        transport_type=TransportType.SMS,
        message_version="20110921",
        message_type="user_message",
        timestamp=datetime(2022, 5, 16, 13, 14, 15, 123456),
        routing_metadata={"test": "routing"},
        helper_metadata={"test": "helper"},
        message_id="7c9af210bc1b4718ae74a1aa026b4757",
        in_reply_to="c333975405074a89bf5d686ac64cc4d3",
        provider="Vodacom",
        session_event=Session.NEW,
        content="test message",
        transport_metadata={"test": "transport"},
        group="test_group",
        to_addr_type=AddressType.MSISDN,
        from_addr_type=AddressType.MSISDN,
    )
    assert msg.serialise() == {
        "to_addr": "+27820001001",
        "from_addr": "12345",
        "transport_name": "test_sms",
        "transport_type": "sms",
        "message_version": "20110921",
        "message_type": "user_message",
        "timestamp": "2022-05-16 13:14:15.123456",
        "routing_metadata": {"test": "routing"},
        "helper_metadata": {"test": "helper"},
        "message_id": "7c9af210bc1b4718ae74a1aa026b4757",
        "in_reply_to": "c333975405074a89bf5d686ac64cc4d3",
        "provider": "Vodacom",
        "session_event": "new",
        "content": "test message",
        "transport_metadata": {"test": "transport"},
        "group": "test_group",
        "to_addr_type": "msisdn",
        "from_addr_type": "msisdn",
    }


def test_deserialise_missing_fields():
    """
    Missing fields should raise an exception
    """
    with pytest.raises(Exception) as e_info:
        Message.deserialise({})
    exceptions = e_info.value.exceptions
    missing_fields = ("to_addr", "from_addr", "transport_name", "transport_type")
    for exception in exceptions:
        assert isinstance(exception, KeyError)
        assert exception.args[0] in missing_fields


def test_minimal_fields():
    """
    Minimal fields should fill in with defaults
    """
    data = {
        "to_addr": "+27820001001",
        "from_addr": "12345",
        "transport_name": "test_sms",
        "transport_type": "sms",
    }
    msg = Message.deserialise(data)
    assert msg.serialise() == {
        "to_addr": "+27820001001",
        "from_addr": "12345",
        "transport_name": "test_sms",
        "transport_type": "sms",
        "message_version": "20110921",
        "message_type": "user_message",
        "timestamp": serialise_vumi_timestamp(msg.timestamp),
        "routing_metadata": {},
        "helper_metadata": {},
        "message_id": msg.message_id,
        "in_reply_to": None,
        "provider": None,
        "session_event": None,
        "content": None,
        "transport_metadata": {},
        "group": None,
        "to_addr_type": None,
        "from_addr_type": None,
    }


def test_vumi_timestamp():
    """
    Serialising and deserialising of a vumi timestamp should work as expected
    """
    assert (
        serialise_vumi_timestamp(datetime(2022, 5, 16, 12, 13, 14, 123456))
        == "2022-05-16 12:13:14.123456"
    )
    assert deserialise_vumi_timestamp("2022-05-06 12:13:14", None) == datetime(
        2022, 5, 6, 12, 13, 14
    )
    assert deserialise_vumi_timestamp("2022-05-06 12:13:14.123456", None) == datetime(
        2022, 5, 6, 12, 13, 14, 123456
    )


def test_generate_message_id():
    """
    Should return hex representation of uuid v4
    """
    result = generate_message_id()
    assert isinstance(result, str)
    uuid = UUID(result)
    assert uuid.hex == result
    assert uuid.version == 4
