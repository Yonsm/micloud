import base64
import hashlib
import json
import logging
import os
import random
import string
from urllib import parse
from aiohttp import ClientSession

_LOGGER = logging.getLogger(__package__)


def get_random(length):
    return ''.join(random.sample(string.ascii_letters + string.digits, length))


class MiTokenStore:

    def __init__(self, token_path):
        self.token_path = token_path

    def load_token(self):
        try:
            with open(self.token_path) as f:
                return json.load(f)
        except Exception:
            _LOGGER.exception(f"Exception on load token from {self.token_path}")
        return None

    def save_token(self, token=None):
        if token:
            try:
                with open(self.token_path, 'w') as f:
                    json.dump(token, f, indent=2)
            except Exception:
                _LOGGER.exception(f"Exception on save token to {self.token_path}")
        elif os.path.isfile(self.token_path):
            os.remove(self.token_path)


class MiAccount:

    def __init__(self, session: ClientSession, username, password, token_store='.mi.token'):
        self.session = session
        self.username = username
        self.password = password
        self.token_store = MiTokenStore(token_store) if isinstance(token_store, str) else token_store
        self.token = token_store is not None and self.token_store.load_token()

    async def get_token(self, sid):
        if self.token:
            if sid in self.token:
                return self.token
        else:
            self.token = {'deviceId': get_random(16).upper()}
        try:
            resp = await self._serviceLogin(f'serviceLogin?sid={sid}&_json=true')
            if resp['code'] != 0:
                data = {
                    '_json': 'true',
                    'qs': resp['qs'],
                    'sid': resp['sid'],
                    '_sign': resp['_sign'],
                    'callback': resp['callback'],
                    'user': self.username,
                    'hash': hashlib.md5(self.password.encode()).hexdigest().upper()
                }
                resp = await self._serviceLogin('serviceLoginAuth2', data)
                if resp['code'] != 0:
                    raise Exception(resp)
                self.token['userId'] = resp['userId']
                self.token['ssecurity'] = resp['ssecurity']
                self.token['passToken'] = resp['passToken']

            serviceToken = await self._securityTokenService(resp['location'], resp['nonce'], resp['ssecurity'])
            self.token[sid] = serviceToken
            if self.token_store:
                self.token_store.save_token(self.token)
            return self.token

        except Exception as e:
            self.token = None
            if self.token_store:
                self.token_store.save_token()
            _LOGGER.exception(f"Exception on login {self.username}: {e}")
            return None

    async def _serviceLogin(self, uri, data=None):
        headers = {'User-Agent': 'APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS'}
        cookies = {'sdkVersion': '3.9', 'deviceId': self.token['deviceId']}
        if 'passToken' in self.token:
            cookies['userId'] = self.token['userId']
            cookies['passToken'] = self.token['passToken']
        url = 'https://account.xiaomi.com/pass/' + uri
        r = await self.session.request('GET' if data is None else 'POST', url, data=data, cookies=cookies, headers=headers)
        raw = await r.read()
        resp = json.loads(raw[11:])
        _LOGGER.debug("%s: %s", uri, resp)
        return resp

    async def _securityTokenService(self, location, nonce, ssecurity):
        nsec = 'nonce=' + str(nonce) + '&' + ssecurity
        clientSign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        r = await self.session.get(location + '&clientSign=' + parse.quote(clientSign))
        serviceToken = r.cookies['serviceToken'].value
        if not serviceToken:
            raise Exception(await r.text())
        return serviceToken