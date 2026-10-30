#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A minimal local RyuNET-compatible server for offline development and LAN play.

Real RyuNET (octr.ryu7w7.xyz) is a Demonware server written for this game whose
source is unavailable, so this file re-implements only the client contract that
run.py and src/runtime_*.c actually use. Point the launcher's Online tab (or
bbport.ini) at this server to play on your own machine instead.

What is REAL here (verified against run.py and src/runtime_http.c,
src/runtime_friends.c, src/runtime_room_mode.c):

- GET /ryunet/theme: the season theme, race-end countdown and item rules, in
  the exact JSON shape run.py's setup_online() reads (theme,
  end_race_timeout, items with a name).
- Nickname ownership: GET /ryunet/nick and POST /ryunet/nick/claim verify the
  X-RyuNET-Proof HMAC EXACTLY as run.py's proof_header() computes it
  (HMAC-SHA256 keyed with the key hash over
  "<unix time>\\n<nonce>\\n<METHOD>\\n<target>", header "1.<ts>.<nonce>.<mac>",
  accepted within 5 minutes of the server clock, each nonce once), and the
  claim stores X-RyuNET-Key-Hash first-claim-wins, persisted to a local JSON
  file. A copied signature is refused, as on the real server.
- GET /ryunet/friends (+ text format) and POST /ryunet/friends/<action>,
  /ryunet/friends/search, /ryunet/report, /ryunet/friends/privacy and
  GET/POST /ryunet/room-mode: the endpoints the game's Friends tab, report
  flow and private-room mode sync call. Friend requests between nicknames
  claimed on THIS server are recorded on both sides; everything else answers
  with a documented word (never a 500).

What is STUBBED (logged with a [stub] line, and answered so the game keeps
going instead of failing into RF_UNREACHABLE):

- Demonware auth/lobby/umbrella/uno HTTP traffic (POST /auth/ and any other
  non-/ryunet/ path the game sends to the overridden hosts): answered
  ``{"stub": true, ...}``. The login, sessions and invitations the online
  menus need are already emulated locally by the runtime itself
  (src/runtime_np_online.c, src/runtime_services.c); these stubs only keep
  the game's HTTP requests from failing. The game reaches the online menus
  and private rooms; public matchmaking population and UDP relays are OUT OF
  SCOPE (a single laptop has nobody to match with).

Launcher integration (no code change needed, the Online tab already supports
a custom server):

- Windows launcher: Online tab -> Server: ``127.0.0.1:8080``, set your
  nickname, enable Online, press Play.
- run.py / Linux launcher: bbport.ini ``online=1``,
  ``online_server=127.0.0.1``, ``online_http_port=8080`` (or
  BB_ONLINE_SERVER / BB_ONLINE_HTTP_PORT in the environment).
- Start this server with one command::

    python scripts/ryunet_server.py --port 8080

  Options: --host (default 127.0.0.1), --db (JSON state file, default
  ryunet_local.json next to the current directory), --theme (default,
  halloween, golden), --end-race-timeout (seconds, default 30),
  --items-file (JSON item rules served as the server's "items").
"""
import argparse
import hashlib
import hmac
import json
import sys
import threading
import time
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RESERVED_NICKS = {'ryupc', 'ryunet', 'player', 'jugador', 'guest'}
PROOF_WINDOW = 300  # seconds: the server takes a signature within 5 minutes
PROOF_VERSION = '1'

# Friends actions the game's menu sends (src/runtime_friends.c act()).
FRIEND_ACTIONS = {'request', 'accept', 'decline', 'remove', 'invite'}

# Words the game shows for what its last action did (parsed as "status <word>").
STATUS_WORDS = {
    'request': 'sent',            # recorded locally, and on the peer when known here
    'accept': 'accepted',         # both sides list each other as friends from now on
    'decline': 'declined',
    'remove': 'removed',
    'invite': 'invite-stub',      # STUB: invites need a live peer session (see module docs)
    'report': 'logged',           # STUB: logged locally, no moderators read it
}


def nick_ok(name):
    """A nickname the server accepts: run.py's nick_ok(), mirrored here."""
    return (isinstance(name, str) and 3 <= len(name) <= 16
            and all(c.isascii() and (c.isalnum() or c in '_-') for c in name)
            and name.lower() not in RESERVED_NICKS)


def proof_mac(khash, ts, nonce, method, target):
    """The MAC run.py's proof_header() signs (the server's nick_proof.go)."""
    msg = f'{ts}\n{nonce}\n{method}\n{target}'.encode('utf-8')
    return hmac.new(khash.encode('ascii'), msg, hashlib.sha256).hexdigest()


def verify_proof(khash, method, target, header, now=None, seen=None, window=PROOF_WINDOW):
    """True when header is a fresh signature of (method, target) under khash.

    Mirrors run.py proof_header() and the C runtime_ryunet_proof(): header
    "1.<ts>.<nonce>.<mac>", ts within `window` seconds of `now`, nonce unseen
    (replay refused when `seen` is a dict, which then records it). Returns
    (ok, reason); `reason` is for the log only, never sent to the client.

    NOT thread-safe on its own: callers sharing one `seen` dict across threads
    must hold a lock around the whole call (prune + check + insert), or two
    requests can pass the same nonce. The server holds proof_lock (see
    Handler._proof); single-threaded callers (tests) need none.
    """
    now = time.time() if now is None else now
    try:
        version, ts, nonce, mac = (header or '').split('.')
    except ValueError:
        return False, 'malformed'
    if version != PROOF_VERSION:
        return False, 'version'
    if not (khash and len(khash) == 64
            and all(c in '0123456789abcdef' for c in khash.lower())):
        return False, 'key-hash'
    try:
        age = now - int(ts)
    except ValueError:
        return False, 'time'
    if abs(age) > window:
        return False, 'out-of-time'
    if len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce.lower()):
        return False, 'nonce'
    if seen is not None:
        for old in [n for n, t in seen.items() if now - t > window]:
            del seen[old]
        if nonce in seen:
            return False, 'replay'
    if not hmac.compare_digest(proof_mac(khash.lower(), ts, nonce, method, target), mac.lower()):
        return False, 'mismatch'
    if seen is not None:
        seen[nonce] = now
        if len(seen) > 100000:
            seen.clear()
    return True, 'ok'


class LocalState:
    """Claims, friends and room modes, persisted to one JSON file."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {'claims': {}, 'friends': {}, 'room_mode': {}, 'reports': []}
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                for key in self.data:
                    if isinstance(loaded.get(key), dict if key != 'reports' else list):
                        self.data[key] = loaded[key]
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            tmp.replace(self.path)
        except OSError:
            pass

    def claim_of(self, name):
        return self.data['claims'].get((name or '').lower())

    def friends_of(self, name):
        key = (name or '').lower()
        view = self.data['friends'].setdefault(
            key, {'friends': [], 'incoming': [], 'outgoing': [], 'invites': []})
        for field in ('friends', 'incoming', 'outgoing', 'invites'):
            view.setdefault(field, [])
        return view


class Handler(BaseHTTPRequestHandler):
    server_version = 'RyuNET-local/1.0'

    def log_request(self, code='-', size='-'):
        kind = getattr(self, '_log_kind', 'real')
        sys.stderr.write(f'{self.command} {self.path} -> {code} [{kind}]\n')

    def _send(self, code, body, kind='text/plain; charset=utf-8'):
        self._log_kind = kind.split(';')[0]
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Date', formatdate(timeval=None, localtime=False, usegmt=True))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)

    def _json(self, code, obj, kind='real'):
        self._log_kind = f'{kind}:json' if kind != 'json' else kind
        self._send(code, json.dumps(obj, sort_keys=True), 'application/json')

    def _text(self, code, text, kind):
        self._log_kind = kind
        self._send(code, text)

    def log_message(self, *args):  # noqa: D102 (stderr logging lives in log_request_line)
        pass

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlsplit(self.path).query).items()}

    def _target(self):
        # The proof signs the request target the client sent (path + query).
        split = urllib.parse.urlsplit(self.path)
        target = split.path
        if split.query:
            target += '?' + split.query
        return target

    def _proof(self, khash, method=None):
        method = method or self.command
        header = self.headers.get('X-RyuNET-Proof', '')
        # One lock around prune + check + insert: two threads must never pass
        # the same nonce (ThreadingHTTPServer serves each request on its own).
        with self.server.proof_lock:
            return verify_proof(khash, method, self._target(), header,
                                seen=self.server.state_lock_table)

    # -- /ryunet/theme ----------------------------------------------------
    def _theme(self):
        if self.command != 'GET':
            return self._json(405, {'error': 'method-not-allowed'})
        cfg = self.server.theme_config
        self._json(200, {'theme': cfg['theme'],
                         'end_race_timeout': cfg['end_race_timeout'],
                         'items': cfg['items']})

    # -- /ryunet/nick ------------------------------------------------------
    def _nick_status(self):
        if self.command != 'GET':
            return self._json(405, {'error': 'method-not-allowed'})
        name = self._query().get('name', '')
        if not nick_ok(name):
            return self._json(200, {'status': 'invalid'})
        with self.server.state.lock:
            record = self.server.state.claim_of(name)
        khash = self.headers.get('X-RyuNET-Key-Hash', '').strip().lower()
        if record is None:
            return self._json(200, {'status': 'free'})
        if khash and hmac.compare_digest(record['key_hash'], khash):
            return self._json(200, {'status': 'yours'})
        return self._json(200, {'status': 'taken'})

    def _nick_claim(self):
        if self.command != 'POST':
            return self._json(405, {'error': 'method-not-allowed'})
        name = self._query().get('name', '')
        khash = self.headers.get('X-RyuNET-Key-Hash', '').strip().lower()
        if not nick_ok(name):
            return self._json(200, {'status': 'invalid'})
        ok, reason = self._proof(khash, 'POST')
        if not ok:
            sys.stderr.write(f'nick claim {name}: proof refused ({reason})\n')
            return self._json(403, {'status': 'refused', 'reason': reason})
        with self.server.state.lock:
            record = self.server.state.claim_of(name)
            if record is None:
                self.server.state.data['claims'][name.lower()] = {
                    'key_hash': khash, 'since': int(time.time())}
                self.server.state.save()
                sys.stderr.write(f"Nickname {name}: claimed (first claim wins)\n")
                return self._json(200, {'status': 'claimed'})
            if hmac.compare_digest(record['key_hash'], khash):
                return self._json(200, {'status': 'yours'})
        sys.stderr.write(f'Nickname {name}: taken by another key\n')
        return self._json(403, {'status': 'taken'})

    # -- /ryunet/friends ---------------------------------------------------
    def _friends_view(self, me):
        with self.server.state.lock:
            view = self.server.state.friends_of(me)
            lines = ([f"friend 1 1 8 {n}" for n in view['friends']]
                     + [f"incoming {n}" for n in view['incoming']]
                     + [f"outgoing {n}" for n in view['outgoing']]
                     + [f"invite 0 {i['from']}" for i in view.get('invites', [])])
        return '\n'.join(lines) + ('\n' if lines else '')

    def _friends_owner(self, query):
        """The acting nickname: verified by proof when claimed, else the name."""
        me = query.get('name', '')
        if not nick_ok(me):
            return None
        with self.server.state.lock:
            record = self.server.state.claim_of(me)
        if record is None:
            return me  # unclaimed here (e.g. fresh test nick): still serve, never strand
        ok, _ = self._proof(record['key_hash'])
        return me if ok else None

    def _friends_poll(self):
        if self.command != 'GET':
            return self._text(405, 'status error\n', 'friends:error')
        if self._query().get('format') != 'text':
            return self._text(400, 'status error\n', 'friends:error')
        me = self._friends_owner(self._query())
        if me is None:
            return self._text(403, 'status refused\n', 'friends:refused')
        self._text(200, self._friends_view(me), 'friends:poll')

    def _friends_search(self):
        if self.command != 'GET':
            return self._text(405, 'status error\n', 'friends:error')
        me = self._friends_owner(self._query())
        if me is None:
            return self._text(403, 'status refused\n', 'friends:refused')
        # STUB: no player directory on a local server; the game's own friend
        # list (bbport.ini online_friends=) still works through the runtime.
        self._text(200, '', 'friends:search-stub')

    def _friends_action(self, action):
        if self.command != 'POST':
            return self._text(405, 'status error\n', 'friends:error')
        query = self._query()
        if query.get('format') != 'text':
            return self._text(400, 'status error\n', 'friends:error')
        me = self._friends_owner(query)
        if me is None:
            return self._text(403, 'status refused\n', 'friends:refused')
        if action == 'invite':
            # STUB: real invites wake a live peer session; with no relay the
            # game still reports the word below (never a 500).
            return self._text(200, f"status {STATUS_WORDS['invite']}\n", 'friends:invite-stub')
        who = query.get('to', query.get('from', query.get('who', '')))
        if not nick_ok(who) or who.lower() == me.lower():
            return self._text(200, 'status error\n', 'friends:action')
        with self.server.state.lock:
            mine = self.server.state.friends_of(me)
            peer = self.server.state.friends_of(who)
            word = STATUS_WORDS[action]
            low = who.lower()
            if action == 'request':
                if low not in mine['friends'] and who not in mine['outgoing']:
                    mine['outgoing'].append(who)
                if me not in peer['incoming'] and low not in peer['friends']:
                    peer['incoming'].append(me)
            elif action == 'accept':
                if me in peer['outgoing']:
                    peer['outgoing'].remove(me)
                if who in mine['incoming']:
                    mine['incoming'].remove(who)
                if low not in [f.lower() for f in mine['friends']]:
                    mine['friends'].append(who)
                if me.lower() not in [f.lower() for f in peer['friends']]:
                    peer['friends'].append(me)
            elif action == 'decline':
                if who in mine['incoming']:
                    mine['incoming'].remove(who)
                if me in peer['outgoing']:
                    peer['outgoing'].remove(me)
            elif action == 'remove':
                mine['friends'] = [f for f in mine['friends'] if f.lower() != low]
                peer['friends'] = [f for f in peer['friends'] if f.lower() != me.lower()]
            self.server.state.save()
        self._text(200, f'status {word}\n', 'friends:action')

    def _friends_privacy(self):
        if self.command != 'POST':
            return self._text(405, 'status error\n', 'friends:error')
        me = self._friends_owner(self._query())
        if me is None:
            return self._text(403, 'status refused\n', 'friends:refused')
        # STUB: recorded nowhere (one local player has nobody to hide from).
        self._text(200, '', 'friends:privacy-stub')

    def _report(self):
        if self.command != 'POST':
            return self._text(405, 'status error\n', 'friends:error')
        query = self._query()
        me = self._friends_owner(query)
        if me is None:
            return self._text(403, 'status refused\n', 'friends:refused')
        with self.server.state.lock:
            self.server.state.data['reports'].append({
                'from': me, 'who': query.get('who', ''),
                'reason': query.get('reason', ''),
                'comment': query.get('comment', ''), 'at': int(time.time())})
            self.server.state.save()
        sys.stderr.write(f"report from {me} about {query.get('who', '')}: logged locally (no moderators)\n")
        # Bare word, no "status " prefix: the game reads the first token.
        self._text(200, f"{STATUS_WORDS['report']}\n", 'friends:report-stub')

    # -- /ryunet/room-mode --------------------------------------------------
    def _room_mode(self):
        query = self._query()
        if self.command == 'POST':
            me = self._friends_owner(query)
            if me is None:
                return self._text(403, 'status refused\n', 'room-mode:refused')
            mode = query.get('mode', 'normal')
            mode = mode if mode in ('speed', 'normal') else 'normal'
            with self.server.state.lock:
                self.server.state.data['room_mode'][me.lower()] = mode
                self.server.state.save()
            return self._text(200, f'mode {mode}\n', 'room-mode:publish')
        if self.command == 'GET':
            host = query.get('host', '')
            with self.server.state.lock:
                mode = self.server.state.data['room_mode'].get(host.lower(), 'normal')
            return self._text(200, f'mode {mode}\n', 'room-mode:follow')
        return self._text(405, 'status error\n', 'room-mode:error')

    # -- Demonware stubs ------------------------------------------------------
    def _demonware_stub(self):
        # STUB: auth/lobby/umbrella/uno traffic the game addresses to the real
        # Demonware hosts lands here through host_overrides.json. The runtime
        # emulates login/sessions/invitations locally, so answering 200 keeps
        # the online menus reachable; nothing here is a real backend.
        length = int(self.headers.get('Content-Length') or 0)
        if length > 0:
            try:
                self.rfile.read(min(length, 1 << 20))
            except OSError:
                pass
        sys.stderr.write(f'[stub] Demonware {self.command} {self._target()} '
                         f'(Host: {self.headers.get("Host", "")})\n')
        self._log_kind = 'stub:demonware'
        self._json(200, {'stub': True,
                         'note': 'local RyuNET stand-in: no real Demonware backend '
                                 '(matchmaking/relays out of scope)'}, kind='stub')

    # -- dispatch -------------------------------------------------------------
    def _route(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/ryunet/theme':
            return self._theme()
        if path == '/ryunet/nick':
            return self._nick_status()
        if path == '/ryunet/nick/claim':
            return self._nick_claim()
        if path == '/ryunet/friends':
            return self._friends_poll()
        if path == '/ryunet/friends/search':
            return self._friends_search()
        if path == '/ryunet/friends/privacy':
            return self._friends_privacy()
        if path == '/ryunet/report':
            return self._report()
        if path == '/ryunet/room-mode':
            return self._room_mode()
        if path.startswith('/ryunet/friends/'):
            action = path.rsplit('/', 1)[-1]
            if action in FRIEND_ACTIONS:
                return self._friends_action(action)
            return self._json(404, {'error': f'unknown-friends-action: {action}'})
        if path.startswith('/ryunet/'):
            return self._json(404, {'error': f'unknown-path: {path}'})
        return self._demonware_stub()

    def _safe_route(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # never a 500: log it, answer an error word
            sys.stderr.write(f'handler error on {self.command} {self.path}: {error!r}\n')
            try:
                if urllib.parse.urlsplit(self.path).path.startswith('/ryunet/friends'):
                    self._text(400, 'status error\n', 'friends:error')
                else:
                    self._json(400, {'status': 'error'})
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = do_POST = lambda self: self._safe_route()  # noqa: E731
    do_HEAD = lambda self: self._safe_route()  # noqa: E731


def make_server(host, port, db, theme_config):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.state = LocalState(db)
    server.state_lock_table = {}
    server.proof_lock = threading.Lock()
    server.theme_config = theme_config
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--db', default='ryunet_local.json',
                        help='JSON file with nick claims, friends and room modes')
    parser.add_argument('--theme', default='default', choices=['default', 'halloween', 'golden'])
    parser.add_argument('--end-race-timeout', type=float, default=30)
    parser.add_argument('--items-file', default=None,
                        help='JSON item rules served as the server "items"')
    args = parser.parse_args(argv)
    items = {'name': 'local-default'}
    if args.items_file:
        items = json.loads(Path(args.items_file).read_text(encoding='utf-8'))
        if not isinstance(items, dict):
            parser.error('--items-file must hold a JSON object')
    server = make_server(args.host, args.port, args.db,
                         {'theme': args.theme,
                          'end_race_timeout': args.end_race_timeout,
                          'items': items})
    sys.stderr.write(f'Local RyuNET on {args.host}:{server.server_address[1]} '
                     f'(db {args.db}, theme {args.theme})\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
