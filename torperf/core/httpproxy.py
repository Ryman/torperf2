from twisted.web import http, proxy
from twisted.internet import reactor, interfaces
from twisted.internet.endpoints import TCP4ClientEndpoint
from txsocksx.http import SOCKS5ClientEndpoint
import urlparse

class MeasuredHttpProxyClient(proxy.ProxyClient):
    def __init__(self, command, rest, version, headers, data, father):
        proxy.ProxyClient.__init__(self, command, rest, version, headers, data, father)
        self.data = data
        self.timer = interfaces.IReactorTime(reactor)
        self.timed_out = 0
        self.decileLogged = 0
        self.receivedBytes = 0
        self.sentBytes = 0
        # Modify outgoing headers here via self.father
        father.times = {}
        self.setUniqueID()

    def setUniqueID(self):
        self.handleHeader('X-TorPerfProxyId', 42)

    def connectionMade(self):
        self.father.times['DATAREQUEST'] = "%.02f" % self.timer.seconds()
        proxy.ProxyClient.connectionMade(self)

    """Mange returned header, content here.

    Use `self.father` methods to modify request directly.
    """
    def handleHeader(self, key, value):
        # change response header here
        # print "Header: %s: %s" % (key, value)
        proxy.ProxyClient.handleHeader(self, key, value)

    def handleResponsePart(self, buffer):
        # change response part here
        if self.receivedBytes == 0 and len(buffer) > 0:
            self.father.times['DATARESPONSE'] = "%.02f" % self.timer.seconds()
        self.receivedBytes += len(buffer)

        # make all content upper case
        proxy.ProxyClient.handleResponsePart(self, buffer.upper())

    def handleStatus(self, version, code, message):
        if code != "200":
            print "Got status: %s for %s" % (code, self.father.uri)
            print "Message was: %s" % message
        proxy.ProxyClient.handleStatus(self, version, code, message)

    def handleResponseEnd(self):
        if not self._finished:
            self.father.times['WRITEBYTES'] = self.sentBytes
            self.father.times['READBYTES'] = self.receivedBytes
            self.father.times['DATACOMPLETE'] = "%.02f" % self.timer.seconds()
            self.father.times['DIDTIMEOUT'] = self.timed_out
            self.father.times['FINALURI'] = self.father.uri
            proxy.ProxyClient.handleResponseEnd(self)

    def timeout(self):
        self.timed_out = 1
        self.handleResponseEnd()

class MeasuredHttpProxyClientFactory(proxy.ProxyClientFactory):
    protocol = MeasuredHttpProxyClient
    def __init__(self, url, *args, **kwargs):
        proxy.ProxyClientFactory.__init__(self, url, *args, **kwargs)

    def buildProtocol(self, addr):
        #TODO: Add a timeout
        return self.protocol(self.command, self.rest, self.version,
                        self.headers, self.data, self.father)

    def clientConnectionFailed(self, connector, reason):
        """
        Report a connection failure in a response to the incoming request as
        an error.
        """
        self.father.setResponseCode(501, "Gateway error")
        self.father.responseHeaders.addRawHeader("Content-Type", "text/html")
        self.father.write("<H1>Could not connect</H1>")
        self.father.write(str(reason))

        print "Errored: %s" % str(reason)

        if hasattr(self.father, 'times'):
            print "Errored: %s" % self.father.times

        self.father.finish()

class MeasuredHttpProxyRequest(proxy.ProxyRequest):
    protocols = dict(http=MeasuredHttpProxyClientFactory)

    def __init__(self, channel, queued, reactor=reactor):
        proxy.ProxyRequest.__init__(self, channel, queued, reactor)
        # TODO: Take Tor socks port

    def process(self):
        print "Proxying request: %s" % self.uri
        parsed = urlparse.urlparse(self.uri)
        protocol = parsed[0]
        host = parsed[1]
        if protocol != "http":
            print "Skipping unimplemented protocol: %s" % protocol
            self.finish()
            return
        port = self.ports[protocol]
        if ':' in host:
            host, port = host.split(':')
            port = int(port)
        rest = urlparse.urlunparse(('', '') + parsed[2:])
        if not rest:
            rest = rest + '/'
        class_ = self.protocols[protocol]
        headers = self.getAllHeaders().copy()
        if 'host' not in headers:
            headers['host'] = host
        self.content.seek(0, 0)
        s = self.content.read()
        clientFactory = class_(self.method, rest, self.clientproto, headers,
                               s, self)

        torEndpoint = TCP4ClientEndpoint(self.reactor, '127.0.0.1', 9050)
        socksEndpoint = SOCKS5ClientEndpoint(host, port, torEndpoint)

        socksReq = socksEndpoint.connect(clientFactory)
        def socks_error(reason):
            print "SOCKS ERROR: %s" % str(reason)
            self.connectionLost(reason)
        socksReq.addErrback(socks_error)

class MeasuredHttpProxy(proxy.Proxy):
    requestFactory = MeasuredHttpProxyRequest

class MeasuredHttpProxyFactory(http.HTTPFactory):
    #TODO TAKE A SOCKS PORT
    protocol = MeasuredHttpProxy