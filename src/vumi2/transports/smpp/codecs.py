import codecs

GSM0338_CHARSET = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM0338_CHARSET_MAP = {c: i for i, c in enumerate(GSM0338_CHARSET)}
GSM0338_CHARSET_EXTENSION = (
    "````````````````````^```````````````````{}`````\\````````````[~]`"
    "|````````````````````````````````````€``````````````````````````"
)
GSM0338_CHARSET_EXTENSION_MAP = {c: i for i, c in enumerate(GSM0338_CHARSET_EXTENSION)}
GSM0338_CHARSET_EXTENSION_ESCAPE = 27


class VumiCodecException(Exception):
    """
    For any issues when encoding or decoding using vumi codecs, that aren't handled
    by built-in python exceptions
    """


class Gsm0338Codec(codecs.Codec):
    """
    Codec for https://en.wikipedia.org/wiki/GSM_03.38
    """

    NAME = "gsm0338"

    def encode(self, input: str, errors: str = "strict") -> tuple[bytes, int]:
        """
        Modified from https://stackoverflow.com/a/2453027
        """
        result = []
        for position, char in enumerate(input):
            idx = GSM0338_CHARSET_MAP.get(char)
            if idx is not None:
                result.append(idx)
                continue
            idx = GSM0338_CHARSET_EXTENSION_MAP.get(char)
            if idx is not None:
                result.append(GSM0338_CHARSET_EXTENSION_ESCAPE)
                result.append(idx)
                continue
            if errors == "strict":
                raise UnicodeEncodeError(
                    self.NAME, char, position, position + 1, repr(input)
                )
            elif errors == "ignore":
                continue
            elif errors == "replace":
                result.append(GSM0338_CHARSET_MAP["?"])
            else:
                raise VumiCodecException(
                    f"Invalid errors type {errors} for {self.NAME} codec encode"
                )
        return (bytes(result), len(result))

    def decode(self, input: bytes, errors: str = "strict") -> tuple[str, int]:
        """
        Modified from https://stackoverflow.com/a/13131694
        """
        res = iter(input)
        result = []
        for position, char in enumerate(res):
            try:
                if char == GSM0338_CHARSET_EXTENSION_ESCAPE:
                    char = next(res)
                    result.append(GSM0338_CHARSET_EXTENSION[char])
                else:
                    result.append(GSM0338_CHARSET[char])
            except IndexError as e:
                if errors == "strict":
                    raise UnicodeDecodeError(
                        self.NAME, bytes(char), position, position + 1, repr(input)
                    ) from e
                elif errors == "ignore":
                    continue
                elif errors == "replace":
                    result.append("�")
                else:
                    raise VumiCodecException(
                        f"Invalid errors type {errors} for {self.NAME} codec decode"
                    ) from e
        return ("".join(result), len(result))


def register_codecs():
    gsm0338 = Gsm0338Codec()
    CODECS = {
        # The type for decode wants a more general interface than `bytes`, but
        # the way we're using it is fine.
        gsm0338.NAME: codecs.CodecInfo(
            name=gsm0338.NAME,
            encode=gsm0338.encode,
            decode=gsm0338.decode,  # type: ignore
        )
    }
    codecs.register(CODECS.get)
