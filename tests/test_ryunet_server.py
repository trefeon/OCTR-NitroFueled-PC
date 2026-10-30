"""The local RyuNET server (scripts/ryunet_server.py): proof verification and
endpoint smoke tests. No game needed: an in-process server and urllib."""
import hashlib
import hmac
import json
import secrets
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from paths import ROOT  # noqa: F401 (scripts/ on the path)
import sys

sys.path.insert(0, str(ROOT))

import run
from ryunet_server import Handler, LocalState, make_server, nick_ok, verify_proof


def make_proof(khash, method, target):
    """run.py's proof_header(), without touching its clock globals."""
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    mac = hmac.new(khash.encode('ascii'), f'{ts}\n{nonce}\n{method}\n{target}'.encode('utf-8'),
                   hashlib.sha256).hexdigest()
    return f'1.{ts}.{nonce}.{mac}'


class ProofTests(unittest.TestCase):
    KHASH = hashlib.sha256(b'key').hexdigest()

    def test_valid_proof_verifies(self):
        ok, reason = verify_proof(self.KHASH, 'POST', '/ryunet/nick/claim?name=A',
                                  make_proof(self.KHASH, 'POST', '/ryunet/nick/claim?name=A'),
                                  seen={})
        self.assertTrue(ok, reason)

    def test_run_py_proof_header_verifies(self):
        target = '/ryunet/friends?name=Tester&format=text'
        header = run.proof_header(self.KHASH, 'GET', target)
        self.assertTrue(verify_proof(self.KHASH, 'GET', target, header, seen={})[0])

    def test_forged_key_refused(self):
        other = hashlib.sha256(b'other').hexdigest()
        header = make_proof(other, 'GET', '/ryunet/theme')
        self.assertEqual(verify_proof(self.KHASH, 'GET', '/ryunet/theme', header, seen={}),
                         (False, 'mismatch'))

    def test_tampered_target_refused(self):
        header = make_proof(self.KHASH, 'GET', '/ryunet/theme')
        ok, reason = verify_proof(self.KHASH, 'GET', '/ryunet/theme?x=1', header, seen={})
        self.assertEqual((ok, reason), (False, 'mismatch'))

    def test_tampered_method_refused(self):
        header = make_proof(self.KHASH, 'GET', '/ryunet/theme')
        self.assertEqual(verify_proof(self.KHASH, 'POST', '/ryunet/theme', header, seen={}),
                         (False, 'mismatch'))

    def test_expired_proof_refused(self):
        old = make_proof(self.KHASH, 'GET', '/ryunet/theme')
        ts, nonce, mac = old.split('.')[1:]
        stale = f'1.{int(ts) - 3600}.{nonce}.{mac}'
        # Re-sign at the old time so only the age is wrong.
        ts2 = str(int(time.time()) - 3600)
        mac2 = hmac.new(self.KHASH.encode('ascii'),
                        f'{ts2}\n{nonce}\nGET\n/ryunet/theme'.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(verify_proof(self.KHASH, 'GET', '/ryunet/theme',
                                      f'1.{ts2}.{nonce}.{mac2}', seen={}),
                         (False, 'out-of-time'))
        self.assertIn(stale.split('.')[0], ('1',))  # the stale shape still parses

    def test_replayed_nonce_refused(self):
        seen = {}
        header = make_proof(self.KHASH, 'GET', '/ryunet/theme')
        self.assertTrue(verify_proof(self.KHASH, 'GET', '/ryunet/theme', header, seen=seen)[0])
        self.assertEqual(verify_proof(self.KHASH, 'GET', '/ryunet/theme', header, seen=seen),
                         (False, 'replay'))

    def test_malformed_headers_refused(self):
        for bad in ('', '1.123', '2.1.2.3', '1.notatime.' + 'ab' * 16 + '.00', '1.1.2.3.4'):
            ok, _ = verify_proof(self.KHASH, 'GET', '/ryunet/theme', bad, seen={})
            self.assertFalse(ok, bad)

    def test_nick_rules_mirror_run_py(self):
        for good in ('Tester', 'a-b_c', 'Xy9' + 'z' * 13):
            self.assertTrue(nick_ok(good), good)
            self.assertTrue(run.nick_ok(good), good)
        for bad in ('ab', 'x' * 17, 'has space', 'RyuPC', 'PLAYER', 'dot.name', ''):
            self.assertFalse(nick_ok(bad), bad)
            self.assertFalse(run.nick_ok(bad), bad)


class ServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        db = str(Path(cls.tmp.name) / 'ryunet.json')
        cls.server = make_server('127.0.0.1', 0, db,
                                 {'theme': 'halloween', 'end_race_timeout': 30,
                                  'items': {'name': 'local-default'}})
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        host, port = cls.server.server_address[:2]
        cls.base = f'http://{host}:{port}'
        cls.key = secrets.token_hex(32)
        cls.skey = run.server_key(cls.key, host)
        cls.khash = run.key_hash(cls.skey)
        cls.other_khash = run.key_hash(run.server_key(secrets.token_hex(32), host))

    def call(self, method, target, khash=None, key_hash=None):
        headers = {}
        if khash is not None:
            headers['X-RyuNET-Proof'] = make_proof(khash, method, target)
        if key_hash is not None:
            headers['X-RyuNET-Key-Hash'] = key_hash
        request = urllib.request.Request(self.base + target, method=method,
                                         data=b'' if method == 'POST' else None,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, reply.headers.get('Content-Type', ''), reply.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers.get('Content-Type', ''), error.read()

    def test_theme_shape_matches_run_py(self):
        status, kind, body = self.call('GET', '/ryunet/theme')
        self.assertEqual(status, 200)
        self.assertIn('json', kind)
        settings = json.loads(body)
        self.assertIn(settings['theme'], ('default', 'halloween', 'golden'))
        self.assertTrue(10 <= settings['end_race_timeout'] <= 120)
        self.assertIsInstance(settings['items'], dict)
        self.assertIn('name', settings['items'])

    def test_nick_claim_conflict(self):
        nick, other = 'SmokeTester', 'OtherNick'
        q = urllib.parse.urlencode({'name': nick})
        status, _, body = self.call('GET', f'/ryunet/nick?{q}')
        self.assertEqual((status, json.loads(body)['status']), (200, 'free'))
        # No proof: refused, never a 500.
        status, _, body = self.call('POST', f'/ryunet/nick/claim?{q}')
        self.assertEqual(status, 403)
        self.assertIn(json.loads(body)['status'], ('refused', 'taken'))
        # First claim wins.
        status, _, body = self.call(
            'POST', f'/ryunet/nick/claim?{q}', self.khash, self.khash)
        self.assertEqual((status, json.loads(body)['status']), (200, 'claimed'))
        # Same key again: still theirs.
        status, _, body = self.call(
            'POST', f'/ryunet/nick/claim?{q}', self.khash, self.khash)
        self.assertEqual(json.loads(body)['status'], 'yours')
        # Another key: taken.
        status, _, body = self.call(
            'POST', f'/ryunet/nick/claim?{q}', self.other_khash, self.other_khash)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)['status'], 'taken')
        # Status reads back accordingly.
        status, _, body = self.call('GET', f'/ryunet/nick?{q}')
        self.assertEqual(json.loads(body)['status'], 'taken')
        qo = urllib.parse.urlencode({'name': other})
        status, _, body = self.call('GET', f'/ryunet/nick?{qo}')
        self.assertEqual(json.loads(body)['status'], 'free')

    def test_friends_empty_list_works(self):
        nick = 'LonelyRacer'
        q = urllib.parse.urlencode({'name': nick, 'format': 'text'})
        status, _, body = self.call('GET', f'/ryunet/friends?{q}')
        self.assertEqual((status, body), (200, b''))
        # Actions answer words, never 500s.
        for action, arg in (('request', 'to'), ('accept', 'from'),
                            ('decline', 'from'), ('remove', 'who'), ('invite', 'to')):
            with self.subTest(action=action):
                qq = urllib.parse.urlencode({'name': nick, 'format': 'text', arg: 'NobodyHere'})
                status, _, body = self.call('POST', f'/ryunet/friends/{action}?{qq}')
                self.assertEqual(status, 200)
                self.assertTrue(body.startswith(b'status '))
        for path in ('/ryunet/friends/search?' + q, '/ryunet/friends/privacy?' + q):
            with self.subTest(path=path):
                status, _, _ = self.call('POST' if 'privacy' in path else 'GET', path)
                self.assertEqual(status, 200)
        qr = urllib.parse.urlencode({'name': nick, 'who': 'X', 'reason': 'cheating',
                                     'comment': 'test', 'format': 'text'})
        status, _, body = self.call('POST', f'/ryunet/report?{qr}')
        self.assertEqual((status, body), (200, b'logged\n'))

    def test_friends_two_sides_sync(self):
        alice, bob = 'AliceLocal', 'BobLocal'
        for name, kh in ((alice, self.khash), (bob, self.other_khash)):
            q = urllib.parse.urlencode({'name': name})
            self.call('POST', f'/ryunet/nick/claim?{q}', kh, kh)
        qa = urllib.parse.urlencode({'name': alice, 'format': 'text', 'to': bob})
        status, _, body = self.call('POST', f'/ryunet/friends/request?{qa}', self.khash)
        self.assertEqual(body, b'status sent\n')
        qb = urllib.parse.urlencode({'name': bob, 'format': 'text'})
        _, _, incoming = self.call('GET', f'/ryunet/friends?{qb}', self.other_khash)
        self.assertIn(b'incoming AliceLocal', incoming)
        qacc = urllib.parse.urlencode({'name': bob, 'format': 'text', 'from': alice})
        _, _, body = self.call('POST', f'/ryunet/friends/accept?{qacc}', self.other_khash)
        self.assertEqual(body, b'status accepted\n')
        _, _, view = self.call('GET', f'/ryunet/friends?{qb}', self.other_khash)
        self.assertIn(b'friend 1 1 8 AliceLocal', view)

    def test_room_mode_round_trip(self):
        host = 'RoomHost'
        q = urllib.parse.urlencode({'name': host})
        self.call('POST', f'/ryunet/nick/claim?{q}', self.khash, self.khash)
        qp = urllib.parse.urlencode({'name': host, 'format': 'text', 'mode': 'speed'})
        status, _, body = self.call('POST', f'/ryunet/room-mode?{qp}', self.khash)
        self.assertEqual(body, b'mode speed\n')
        qg = urllib.parse.urlencode({'host': host, 'format': 'text'})
        _, _, body = self.call('GET', f'/ryunet/room-mode?{qg}')
        self.assertEqual(body, b'mode speed\n')

    def test_unknown_paths_never_500(self):
        status, _, body = self.call('GET', '/ryunet/bogus')
        self.assertEqual(status, 404)
        self.assertIn('json', _)
        status, kind, body = self.call('POST', '/auth/')
        self.assertEqual(status, 200)
        self.assertIn('json', kind)
        self.assertTrue(json.loads(body)['stub'])

    def test_claims_persist(self):
        nick = 'PersistNick'
        q = urllib.parse.urlencode({'name': nick})
        status, _, body = self.call('POST', f'/ryunet/nick/claim?{q}', self.khash, self.khash)
        self.assertEqual(status, 200)
        db = Path(self.tmp.name) / 'ryunet.json'
        claims = json.loads(db.read_text(encoding='utf-8'))['claims']
        self.assertEqual(claims[nick.lower()]['key_hash'], self.khash)
        self.assertEqual(LocalState(str(db)).claim_of(nick)['key_hash'], self.khash)


if __name__ == '__main__':
    unittest.main()
