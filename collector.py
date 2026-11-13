from cache import Cache, normalized


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
        selected = self.cache.choose(url, request)
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
            body, merged = self.cache.merge_304(url, received)
        else:
            raise ValueError('STATUS')
        return self.cache.save(url, request, selected, body, merged, response.status)

    def close(self):
        self.cache.close()
