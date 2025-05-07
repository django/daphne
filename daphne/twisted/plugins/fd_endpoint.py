import socket

from twisted.internet import endpoints
from twisted.internet.interfaces import IStreamServerEndpointStringParser
from twisted.plugin import IPlugin
from zope.interface import implementer


@implementer(IPlugin, IStreamServerEndpointStringParser)
class _FDParser:
    prefix = "fd"

    def _parseServer(self, reactor, fileno, domain=socket.AF_INET):
        fileno = int(fileno)
        return endpoints.AdoptedStreamServerEndpoint(reactor, fileno, domain)

    def parseStreamServer(self, reactor, *args, **kwargs):
        # Delegate to another function with a sane signature.  This function has
        # an insane signature to trick zope.interface into believing the
        # interface is correctly implemented.
        return self._parseServer(reactor, *args, **kwargs)


parser = _FDParser()
