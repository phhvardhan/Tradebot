import hashlib
import hmac
import io
import json
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from signaldesk.account import AccountError, AccountMonitor, NoRedirect, ReadOnlyAccount

class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.client=ReadOnlyAccount('test-key', 'test-secret')
    def test_signs_exact_query_and_sends_only_get(self):
        response=io.BytesIO(b'{"accountType":"default"}')
        with patch.object(self.client._opener,'open',return_value=response) as send, patch('signaldesk.account.time.time',return_value=1234):
            self.client._get('/sapi/v1/account')
        request=send.call_args.args[0]
        query='recvWindow=5000&timestamp=1234000'
        signature=hmac.new(b'test-secret',query.encode(),hashlib.sha256).hexdigest()
        self.assertEqual(request.full_url,'https://api.wazirx.com/sapi/v1/account?'+query+'&signature='+signature)
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.get_header('X-api-key'),'test-key')
    def test_order_and_withdrawal_endpoints_refused_before_network(self):
        with patch.object(self.client._opener,'open') as send:
            for path in ['/sapi/v1/order','/sapi/v1/crypto/withdraw','https://example.org','/sapi/v1/funds?foo=1']:
                with self.assertRaises(AccountError): self.client._get(path)
            send.assert_not_called()
    def test_redirect_refused(self):
        with self.assertRaises(AccountError): NoRedirect().redirect_request(None,None,302,'',{},'https://example.org')
    def test_network_error_does_not_expose_key_or_signed_url(self):
        with patch.object(self.client._opener,'open',side_effect=OSError('test-secret https://secret-url')):
            with self.assertRaises(AccountError) as err: self.client._get('/sapi/v1/account')
        self.assertNotIn('test-secret',str(err.exception))
        self.assertNotIn('secret-url',str(err.exception))
    def test_funds_keep_decimal_precision_and_omit_zero_balances(self):
        payload=[{'asset':'btc','free':'0.123456789123456789','locked':'0'}, {'asset':'inr','free':'0','locked':'0'}]
        with patch.object(self.client,'_get',side_effect=[{'accountType':'default'},payload]): result=self.client.fetch()
        self.assertEqual(result['balances'],[{'asset':'BTC','free':'0.123456789123456789','locked':'0','reserved_fee':'0'}])
    def test_invalid_funds_refused(self):
        for amount in ['NaN','Infinity','-1']:
            with self.subTest(amount=amount), patch.object(self.client,'_get',side_effect=[{'accountType':'default'},[{'asset':'inr','free':amount,'locked':'0'}]]):
                with self.assertRaises(AccountError): self.client.fetch()
    def test_account_flags_are_not_exposed_as_key_permissions(self):
        with patch.object(self.client,'_get',side_effect=[{'accountType':'default','canTrade':True,'canWithdraw':True},[]]): result=self.client.fetch()
        self.assertNotIn('canTrade',result)
        self.assertNotIn('canWithdraw',result)

class MonitorTests(unittest.TestCase):
    def test_disabled_by_default(self):
        monitor=AccountMonitor()
        self.assertFalse(monitor.refresh())
        self.assertFalse(monitor.snapshot()['enabled'])
    def test_refresh_failure_marks_previous_balances_stale(self):
        client=Mock()
        client.fetch.side_effect=[{'balances':[{'asset':'INR','free':'25'}],'updated':time.time()},AccountError('Offline')]
        monitor=AccountMonitor(client)
        with patch('signaldesk.account.time.monotonic',side_effect=[100,116]):
            monitor.refresh();monitor.refresh()
        result=monitor.snapshot()
        self.assertTrue(result['stale'])
        self.assertEqual(result['error'],'Offline')
        self.assertEqual(result['balances'][0]['free'],'25')
    def test_refresh_throttled_and_not_background_polling(self):
        client=Mock();client.fetch.return_value={'balances':[],'updated':time.time()}
        monitor=AccountMonitor(client)
        with patch('signaldesk.account.time.monotonic',side_effect=[100,101]):
            self.assertTrue(monitor.refresh())
            self.assertFalse(monitor.refresh())
        self.assertEqual(client.fetch.call_count,1)
    def test_snapshot_stales_after_sixty_seconds(self):
        monitor=AccountMonitor(Mock())
        monitor.state.update(stale=False,updated=100)
        with patch('signaldesk.account.time.time',return_value=161):self.assertTrue(monitor.snapshot()['stale'])

if __name__=='__main__':unittest.main()
