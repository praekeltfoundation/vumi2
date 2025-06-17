from confmodel import Config

class BaseMiddlewareConfig(Config):
    """
    Config class for the base middleware.
    """

    

class BaseMiddleware(object):

    CONFIG_CLASS = BaseMiddlewareConfig

    def __init__(self, name, config):
        self.name = name
        self.config = self.CONFIG_CLASS(config, static=True)

    def setup(self):
        pass
    
    def teardown_middleware(self):
        pass


    def inbound_enabled(self, connector_name):
        return False

    def outbound_enabled(self, connector_name):

        return False

    def event_enabled(self, connector_name):
        return False

    def handle_inbound(self, message):

        return message
    
    def handle_outbound(self, message):

        return message
    
    def handle_event(self, message):

        return message

