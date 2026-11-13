from html.parser import HTMLParser


class TitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.inside = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def extract(body, headers, status):
    parser = TitleParser()
    parser.feed(body)
    return {
        'title': ''.join(parser.parts),
        'body': body,
        'etag': headers.get('etag'),
        'content_language': headers.get('content-language'),
        'origin_status': status,
    }
