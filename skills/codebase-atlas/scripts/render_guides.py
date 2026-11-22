import base64
import mimetypes
import os
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


class GuideHTML(HTMLParser):
    tags = set('p h1 h2 h3 h4 h5 h6 ul ol li strong em code pre blockquote hr br a img table thead tbody tr th td del details summary div span'.split())
    voids = {'hr', 'br', 'img'}

    def __init__(self, view, views, resolver):
        super().__init__()
        self.view = view
        self.resolver = resolver
        self.path = (resolver.root / view['document']).resolve()
        self.documents = {(resolver.root / item['document']).resolve(): item['id'] for item in views}
        self.parts = []

    def url(self, value, image=False):
        url = urlsplit(value)
        if url.scheme or url.netloc:
            if url.scheme not in ('https', 'http', 'mailto') or image:
                raise ValueError(f'Unsupported documentation URL: {value}')
            return value
        path = (self.path.parent / unquote(url.path)).resolve() if url.path else self.path
        if not path.is_relative_to(self.resolver.root):
            raise ValueError(f'Documentation path outside repository: {value}')
        if image:
            if path.suffix.lower() not in ('.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif'):
                raise ValueError(f'Unsupported documentation image: {value}')
            media = mimetypes.guess_type(path.name)[0]
            return f'data:{media};base64,' + base64.b64encode(path.read_bytes()).decode()
        if path in self.documents:
            return '#' + self.documents[path] + ('/' + quote(unquote(url.fragment), safe='') if url.fragment else '')
        if path.name == 'index.html' and path.parent.name == 'codebase-atlas' and url.fragment:
            return '#' + url.fragment
        relative = path.relative_to(self.resolver.root).as_posix()
        base = self.resolver.source_base
        if base:
            return base.rstrip('/') + '/' + quote(relative) + ('#' + url.fragment if url.fragment else '')
        return quote(Path(os.path.relpath(path, self.resolver.output)).as_posix()) + ('#' + url.fragment if url.fragment else '')

    def handle_starttag(self, tag, attrs):
        if tag not in self.tags:
            return
        safe = []
        for key, value in attrs:
            if key in ('id', 'class', 'title', 'alt'):
                safe.append((key, value or ''))
            elif tag == 'a' and key == 'href':
                safe.append((key, self.url(value or '')))
            elif tag == 'img' and key == 'src':
                safe.append((key, self.url(value or '', image=True)))
        if tag == 'a' and any(key == 'href' and not value.startswith('#') for key, value in safe):
            safe.extend([('target', '_blank'), ('rel', 'noopener')])
        self.parts.append('<' + tag + ''.join(f' {key}="{escape(value, quote=True)}"' for key, value in safe) + '>')

    def handle_endtag(self, tag):
        if tag in self.tags and tag not in self.voids:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(escape(data))


def build_guides(views, resolver):
    if not views:
        return []
    try:
        import markdown
    except ImportError as error:
        raise RuntimeError('Documentation views require Markdown. Run with: uv run --with Markdown python scripts/render_atlas.py ...') from error
    guides = []
    for view in views:
        resolver.symbols(view['document'])
        text = (resolver.root / view['document']).read_text(encoding='utf-8')
        images = iter(view.get('mermaidImages', []))

        def diagram(match):
            try:
                path = next(images)
            except StopIteration as error:
                raise ValueError(f"Render Mermaid diagrams first and set mermaidImages: {view['document']}") from error
            return f'![{view["title"]} diagram]({path})'

        rendered_text = re.sub(r'^```mermaid\s*\n.*?^```[ \t]*$', diagram, text, flags=re.M | re.S)
        if next(images, None) is not None:
            raise ValueError(f"Unused mermaidImages: {view['document']}")
        html = markdown.markdown(rendered_text, extensions=['tables', 'fenced_code', 'toc', 'sane_lists'])
        parser = GuideHTML(view, views, resolver)
        parser.feed(html)
        guides.append({**view, 'html': ''.join(parser.parts), 'searchText': text,
                       'sourceHref': resolver.resolve(view['document'])['href']})
    return guides
