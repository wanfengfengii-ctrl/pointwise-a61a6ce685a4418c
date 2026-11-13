from transport import Exchange, Response

URL = 'https://notices.example.test/bulletin'


def page(title, language, etag, vary='Accept-Language', control=None):
    headers = {'ETag': etag, 'Content-Language': language, 'Vary': vary}
    if control is not None:
        headers['Cache-Control'] = control
    return Response(200, headers, '<title>' + title + '</title>')


def bilingual():
    return [
        Exchange('en', None, page('River notice', 'en', '"e1"')),
        Exchange('zh', None, page('河道公告', 'zh', '"z1"')),
        Exchange('en', '"e1"', Response(304, {'ETag': '"e1"'})),
        Exchange('zh', '"z1"', Response(304, {})),
    ]
