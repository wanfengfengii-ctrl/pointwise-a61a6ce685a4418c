from cache import CONFLICT, Cache, normalized


class Collector:
    def __init__(self, http, path=':memory:'):
        self.http = http
        self.cache = Cache(path)

    async def collect(self, url, headers):
        request = normalized(headers)
        if not isinstance(url, str) or not url:
            raise ValueError('URL')
        if request.get('accept-language') not in ('en', 'zh'):
            raise ValueError('LANGUAGE')
        while True:
            # Selection and the publish-clock stamp happen together, before
            # the GET; no lock or transaction is held across the wait.
            selected = self.cache.choose(url, request)
            stamp = self.cache.stamp(url)
            outgoing = dict(request)
            if selected is not None and selected['headers'].get('etag'):
                outgoing['if-none-match'] = selected['headers']['etag']
            response = await self.http.get(url, outgoing)
            received = normalized(response.headers)
            if response.status == 200:
                body, merged = response.body, received
            elif response.status == 304:
                if selected is None:
                    raise ValueError('UNSOLICITED_304')
                # A 304 refreshes the representation this request actually
                # selected: its body, response headers replace same-named
                # stored headers, absent headers are inherited.
                body = selected['body']
                merged = dict(selected['headers'])
                merged.update(received)
            else:
                raise ValueError('STATUS')
            record = self.cache.accept(url, request, selected, stamp,
                                       body, merged, response.status)
            if record is not CONFLICT:
                return record
            # A concurrent publication changed the involved scope after this
            # request was sent: discard the late response, re-select, retry.

    def close(self):
        self.cache.close()
