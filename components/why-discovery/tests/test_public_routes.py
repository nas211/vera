import os,socket,unittest
from unittest.mock import Mock,patch
from discovery.market import public_socket,FeedMerge

class RouteTests(unittest.TestCase):
    def test_separate_families_preserve_hostname_tls_and_owned_socket(self):
        for family,label,address in ((socket.AF_INET,'IPv4',('192.0.2.1',443)),(socket.AF_INET6,'IPv6',('2001:db8::1',443,0,0))):
            raw=Mock();tls=Mock();ctx=Mock();ctx.wrap_socket.return_value=tls;ws=Mock()
            with patch.dict(os.environ,{},clear=True),patch('discovery.market.socket.getaddrinfo',return_value=[(family,socket.SOCK_STREAM,6,'',address)]) as dns,patch('discovery.market.socket.socket',return_value=raw),patch('discovery.market.ssl.create_default_context',return_value=ctx),patch('websocket.create_connection',return_value=ws) as connect:
                result=public_socket(family=family)
            dns.assert_called_once_with('trade.api.yellow.pro',443,family,socket.SOCK_STREAM)
            raw.connect.assert_called_once_with(address)
            ctx.wrap_socket.assert_called_once_with(raw,server_hostname='trade.api.yellow.pro')
            self.assertIs(connect.call_args.kwargs['socket'],tls)
            self.assertEqual(result.route_family,label)
    def test_configured_proxy_is_never_bypassed(self):
        with patch.dict(os.environ,{'HTTPS_PROXY':'http://proxy.invalid:8080'},clear=True),patch('discovery.market.socket.socket') as sock,patch('websocket.create_connection') as connect:
            public_socket(family=socket.AF_INET)
        sock.assert_not_called();connect.assert_called_once_with('wss://trade.api.yellow.pro/ws',timeout=5)
    def test_failed_tls_closes_transport_and_never_disables_verification(self):
        raw=Mock();ctx=Mock();ctx.wrap_socket.side_effect=OSError('test failure')
        with patch.dict(os.environ,{},clear=True),patch('discovery.market.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('192.0.2.1',443))]),patch('discovery.market.socket.socket',return_value=raw),patch('discovery.market.ssl.create_default_context',return_value=ctx),patch('websocket.create_connection') as connect:
            with self.assertRaises(OSError):public_socket(family=socket.AF_INET)
        raw.close.assert_called_once();connect.assert_not_called()
    def test_health_records_route_and_redacted_error_type(self):
        out=[];merge=FeedMerge(out.append,lambda m:None,clock=lambda:100.)
        merge.receive(0,('route',{'family':'IPv4'},99.))
        merge.receive(0,('transport_error',{'error':'TimeoutError','reason':'PUBLIC_FEED_UNAVAILABLE'},100.))
        merge.receive(0,('gap','PUBLIC_FEED_UNAVAILABLE',100.));merge.tick()
        status=out[-1][1]['streams']['0']
        self.assertEqual(status['route'],{'family':'IPv4'})
        self.assertEqual(status['last_error']['error'],'TimeoutError')
        self.assertFalse(status['connected'])
