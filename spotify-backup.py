#!/usr/bin/env python3

import argparse
import base64
import codecs
import hashlib
import http.client
import http.server
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


class SpotifyAPI:
    # Requires an OAuth token.
    def __init__(self, auth):
        self._auth = auth

    # Gets a resource from the Spotify API and returns the object.
    def get(self, url, params={}, tries=3):
        # Construct the correct URL.
        if not url.startswith('https://api.spotify.com/v1/'):
            url = 'https://api.spotify.com/v1/' + url
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)

        # Try the sending off the request a specified number of times before giving up.
        for _ in range(tries):
            try:
                req = urllib.request.Request(url)
                req.add_header('Authorization', 'Bearer ' + self._auth)
                res = urllib.request.urlopen(req)
                reader = codecs.getreader('utf-8')
                return json.load(reader(res))
            except Exception as err:
                log('Couldn\'t load URL: {} ({})'.format(url, err))
                time.sleep(2)
                log('Trying again...')
        sys.exit(1)

    # The Spotify API breaks long lists into multiple pages. This method automatically
    # fetches all pages and joins them, returning in a single list of objects.
    def list(self, url, params={}):
        response = self.get(url, params)
        items = response['items']
        while response['next']:
            response = self.get(response['next'])
            items += response['items']
        return items

    # Pops open a browser window for a user to log in and authorize API access.
    @staticmethod
    def authorize(client_id, scope):
        redirect_uri = 'http://127.0.0.1:{}/redirect'.format(SpotifyAPI._SERVER_PORT)
        state = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        webbrowser.open('https://accounts.spotify.com/authorize?' + urllib.parse.urlencode({
            'response_type': 'code',
            'client_id': client_id,
            'scope': scope,
            'redirect_uri': redirect_uri,
            'state': state,
            'code_challenge_method': 'S256',
            'code_challenge': challenge
        }))

        # Start a simple, local HTTP server to receive the authorization code.
        server = SpotifyAPI._AuthorizationServer('127.0.0.1', SpotifyAPI._SERVER_PORT, state)
        try:
            while True:
                server.handle_request()
        except SpotifyAPI._Authorization as auth:
            request = urllib.request.Request('https://accounts.spotify.com/api/token',
                data=urllib.parse.urlencode({
                    'grant_type': 'authorization_code',
                    'code': auth.code,
                    'redirect_uri': redirect_uri,
                    'client_id': client_id,
                    'code_verifier': verifier
                }).encode('ascii'),
                headers={'Content-Type': 'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(request) as response:
                token = json.load(response)
            return SpotifyAPI(token['access_token'])

    # The port that the local server listens on. Don't change this,
    # as Spotify only will redirect to certain predefined URLs.
    _SERVER_PORT = 43019

    class _AuthorizationServer(http.server.HTTPServer):
        def __init__(self, host, port, state):
            handler = type('AuthorizationHandler', (SpotifyAPI._AuthorizationHandler,), {'state': state})
            http.server.HTTPServer.__init__(self, (host, port), handler)

        # Disable the default error handling.
        def handle_error(self, request, client_address):
            raise

    class _AuthorizationHandler(http.server.BaseHTTPRequestHandler):
        state = None

        def do_GET(self):
            if self.path.startswith('/redirect'):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if query.get('state', [None])[0] != self.state:
                    self.send_error(400, 'Invalid authorization state')
                    return
                if 'error' in query:
                    self.send_error(400, 'Spotify authorization failed: ' + query['error'][0])
                    return
                code = query.get('code', [None])[0]
                if not code:
                    self.send_error(400, 'Missing authorization code')
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<script>close()</script>Thanks! You may now close this window.')
                raise SpotifyAPI._Authorization(code)

            else:
                self.send_error(404)

        # Disable the default logging.
        def log_message(self, format, *args):
            pass

    class _Authorization(Exception):
        def __init__(self, code):
            self.code = code


def log(str):
    # print('[{}] {}'.format(time.strftime('%I:%M:%S'), str).encode(sys.stdout.encoding, errors='replace'))
    sys.stdout.buffer.write('[{}] {}\n'.format(time.strftime('%I:%M:%S'), str).encode(sys.stdout.encoding, errors='replace'))
    sys.stdout.flush()


def main():
    # Parse arguments.
    parser = argparse.ArgumentParser(description=('Exports your Spotify playlists. By default, opens a browser window '
                                                    + 'to authorize the Spotify Web API, but you can also manually specify'
                                                    + ' an OAuth token with the --token option.'))
    parser.add_argument('--token', metavar='OAUTH_TOKEN', help=('use a Spotify OAuth token (requires the '
                                                                + '`playlist-read-private` permission)'))
    parser.add_argument('--format', default='txt', choices=['json', 'txt'], help='output format (default: txt)')
    parser.add_argument('file', help='output filename', nargs='?')
    args = parser.parse_args()

    # If they didn't give a filename, then just prompt them. (They probably just double-clicked.)
    while not args.file:
        args.file = input('Enter a file name (e.g. playlists.txt): ')

    # Log into the Spotify API.
    if args.token:
        spotify = SpotifyAPI(args.token)
    else:
        spotify = SpotifyAPI.authorize(client_id='5c098bcc800e45d49e476265bc9b6934', scope='playlist-read-private')

    # Get the ID of the logged in user.
    me = spotify.get('me')
    log('Logged in as {display_name} ({id})'.format(**me))

    # List all playlists and all track in each playlist.
    playlists = spotify.list('users/{user_id}/playlists'.format(user_id=me['id']), {'limit': 50})
    for playlist in playlists:
        log('Loading playlist: {name} ({tracks[total]} songs)'.format(**playlist))
        playlist['tracks'] = spotify.list(playlist['tracks']['href'], {'limit': 100})

    # Write the file.
    with open(args.file, 'w', encoding='utf-8') as f:
        # JSON file.
        if args.format == 'json':
            json.dump(playlists, f)

        # Tab-separated file.
        elif args.format == 'txt':
            for playlist in playlists:
                f.write(playlist['name'] + '\r\n')
                for track in playlist['tracks']:
                    try:
                        f.write('{name}\t{artists}\t{album}\t{uri}\r\n'.format(
                            uri=track['track']['uri'],
                            name=track['track']['name'],
                            artists=', '.join([artist['name'] for artist in track['track']['artists']]),
                            album=track['track']['album']['name']
                        ))
                    except TypeError:
                        pass
                f.write('\r\n')
    log('Wrote file: ' + args.file)


if __name__ == '__main__':
    main()
