import os
import httpx

class UpstreamError(Exception):
    def __init__(self, message, uncertain=False):
        self.message, self.uncertain = message, uncertain

class Keitaro:
    def request(self, method, path, body=None):
        url = os.environ.get('KEITARO_URL', '').rstrip('/')
        key = os.environ.get('KEITARO_API_KEY', '')
        if not url.startswith('https://') or not key:
            raise UpstreamError('Настройте KEITARO_URL (HTTPS) и KEITARO_API_KEY на сервере.')
        try:
            with httpx.Client(timeout=httpx.Timeout(45, connect=15), follow_redirects=False, trust_env=True) as client:
                r = client.request(method, url + '/admin_api/v1/' + path, headers={'Api-Key': key}, json=body)
        except httpx.HTTPError:
            raise UpstreamError('Keitaro не ответил. Обновите состояние перед повторной записью.', uncertain=method != 'GET') from None
        if r.is_error:
            # Do not relay upstream HTML, URLs, credentials, or private response bodies.
            raise UpstreamError(f'Keitaro вернул HTTP {r.status_code}. Проверьте доступ и параметры в админке.')
        try:
            return r.json() if r.content else None
        except ValueError:
            raise UpstreamError('Keitaro вернул неожиданный формат ответа.', uncertain=method != 'GET') from None

    def get(self, path): return self.request('GET', path)
    def post(self, path, body): return self.request('POST', path, body)
    def put(self, path, body): return self.request('PUT', path, body)

client = Keitaro()
