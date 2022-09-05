import pytest

from vumi2.transports.smpp.codecs import VumiCodecException, register_codecs

register_codecs()


def test_gsm0338():
    """
    Encoding and decoding gsm0338 standard charset should return the correct bytes
    """
    assert "HÜLK".encode("gsm0338") == bytes([72, 94, 76, 75])
    assert bytes([72, 94, 76, 75]).decode("gsm0338") == "HÜLK"


def test_gsm0338_extended():
    """
    Encoding and decoding gsm0338 extended charset should return the correct bytes
    """
    assert "foo €".encode("gsm0338") == bytes([102, 111, 111, 32, 27, 101])
    assert bytes([102, 111, 111, 32, 27, 101]).decode("gsm0338") == "foo €"


def test_gsm0338_errors():
    """
    Different error types should result in different actions when encoding and decoding
    """
    with pytest.raises(UnicodeEncodeError):
        "Zoë".encode("gsm0338")
    with pytest.raises(UnicodeDecodeError):
        b"Zo\xeb".decode("gsm0338")

    assert "Zoë".encode("gsm0338", errors="replace") == b"Zo?"
    assert b"Zo\xeb".decode("gsm0338", errors="replace") == "Zo�"

    assert "Zoë".encode("gsm0338", errors="ignore") == b"Zo"
    assert b"Zo\xeb".decode("gsm0338", errors="ignore") == "Zo"

    with pytest.raises(VumiCodecException):
        "Zoë".encode("gsm0338", errors="invalid")
    with pytest.raises(VumiCodecException):
        b"Zo\xeb".decode("gsm0338", errors="invalid")
