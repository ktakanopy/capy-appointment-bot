import argparse
import ast
import io
import json
import keyword
import os
import re
import textwrap
import tokenize
from html import escape
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET


SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
INK = "#172e3b"
MUTED = "#466072"
PAPER = "#f5f8fa"
RULE = "#c8d7df"
LINK = "#13677e"
TONES = {
    "entry": ("#e5f3fc", "#397a9e"),
    "normal": ("#ffffff", RULE),
    "data": ("#e7f3f2", "#548887"),
    "success": ("#e7f3f2", "#548887"),
    "failure": ("#fff0eb", "#bd6959"),
}
FONT = "system-ui, -apple-system, sans-serif"
MONO = "ui-monospace, Menlo, monospace"
PYTHON_TOKEN_STYLES = {
    "COMMENT": "comment",
    "NUMBER": "number",
    "OP": "operator",
    "STRING": "string",
}


def element(parent, tag, content=None, **attributes):
    node = ET.Element(
        f"{{{SVG}}}{tag}",
        {key.replace("_", "-"): str(value) for key, value in attributes.items()},
    )
    node.text = content
    if parent is not None:
        parent.append(node)
    return node


def text(parent, content, x, y, *, size=16, color=INK, weight=400, family=FONT):
    element(parent, "text", content, x=x, y=y, fill=color, font_size=size,
            font_weight=weight, font_family=family)


def lines(content, width):
    return textwrap.wrap(content, width=width, break_long_words=False, break_on_hyphens=False)


def rectangle(parent, x, y, width, height, *, fill, stroke="none", rx=12, stroke_width=1):
    element(parent, "rect", x=x, y=y, width=width, height=height, rx=rx,
            fill=fill, stroke=stroke, stroke_width=stroke_width)


class SourceLinks:
    def __init__(self, root, output, source_base=None):
        self.root = root.resolve()
        self.output = output
        self.source_base = source_base
        self.modules = {}

    def symbols(self, file):
        if file not in self.modules:
            path = (self.root / file).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError(f"Missing source file inside repository: {file}")
            index = {}
            if path.suffix == ".py":
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=file)
                for node in tree.body:
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        index[node.name] = node.lineno
                    if isinstance(node, ast.ClassDef):
                        for method in node.body:
                            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                index[f"{node.name}.{method.name}"] = method.lineno
            self.modules[file] = index
        return self.modules[file]

    def resolve(self, ref):
        file, _, symbol = ref.partition("#")
        symbols = self.symbols(file)
        if symbol and symbol not in symbols:
            raise ValueError(f"Unknown definition: {ref}")
        line = symbols[symbol] if symbol else None
        if self.source_base:
            relative = f"{self.source_base.rstrip('/')}/{quote(file)}"
        else:
            relative = quote(Path(os.path.relpath(self.root / file, self.output)).as_posix())
        return {"file": file, "symbol": symbol, "line": line,
                "href": relative + (f"#L{line}" if line else "")}

    def add(self, parent, ref):
        source = self.resolve(ref)
        file, symbol, line = source["file"], source["symbol"], source["line"]
        link = element(parent, "a", href=source["href"])
        element(link, "title", f"{symbol} · {file}:{line}" if line else file)
        return link, line


def start_svg(view, width, height):
    svg = element(None, "svg", viewBox=f"0 0 {width} {height}", width=width,
                  height=height, role="img", aria_label=view["title"])
    element(svg, "title", view["title"])
    element(svg, "desc", view["subtitle"])
    rectangle(svg, 0, 0, width, height, fill=PAPER, rx=0)
    text(svg, view["title"], 40, 56, size=35, weight=700)
    text(svg, view["subtitle"], 42, 89, size=16, color=MUTED)
    return svg


def draw_edge(parent, edge, boxes):
    source, target, *kind = edge
    ax, ay, aw, ah = boxes[source]
    bx, by, bw, bh = boxes[target]
    dashed = kind == ["async"]
    if ay == by:
        if ax < bx:
            start, end = (ax + aw, ay + ah / 2), (bx, by + bh / 2)
        else:
            start, end = (ax, ay + ah / 2), (bx + bw, by + bh / 2)
        points = [start, end]
    elif dashed and ax == bx == 56:
        source_y, target_y = ay + ah / 2, by + bh / 2
        points = [(ax, source_y), (ax - 14, source_y), (bx - 14, target_y), (bx, target_y)]
    else:
        if ay < by:
            start, end = (ax + aw / 2, ay + ah), (bx + bw / 2, by)
        else:
            start, end = (ax + aw / 2, ay), (bx + bw / 2, by + bh)
        middle = (start[1] + end[1]) / 2
        points = [start, (start[0], middle), (end[0], middle), end]
    attrs = {"stroke_dasharray": "6 5"} if dashed else {}
    element(parent, "polyline", points=" ".join(f"{x},{y}" for x, y in points),
            stroke="#557383", stroke_width=2, fill="none", marker_end="url(#arrow)",
            data_from=source, data_to=target, data_kind="async" if dashed else "flow", **attrs)


def draw_graph(view, sources):
    columns = view["columns"]
    width = 1430
    row_height = 197
    gap = 40
    card_width = (width - 112 - (columns - 1) * gap) / columns
    height = 172 + len(view["lanes"]) * row_height
    svg = start_svg(view, width, height)
    defs = element(svg, "defs")
    marker = element(defs, "marker", id="arrow", markerWidth=8, markerHeight=8,
                     refX=7, refY=4, orient="auto")
    element(marker, "path", d="M0 0L8 4L0 8Z", fill="#557383")
    boxes = {}
    for index, lane in enumerate(view["lanes"]):
        y = 123 + index * row_height
        rectangle(svg, 40, y, width - 80, 157, fill="#edf3f6", stroke=RULE)
        occupied = set()
        for column, node in enumerate(lane["nodes"]):
            if node is None:
                continue
            slot = node.get("column", column)
            if slot in occupied or not 0 <= slot < columns or node["id"] in boxes:
                raise ValueError(f"Duplicate or invalid node position: {node['id']}")
            occupied.add(slot)
            boxes[node["id"]] = (56 + slot * (card_width + gap), y + 42, card_width, 99)
    for edge in view["edges"]:
        draw_edge(svg, edge, boxes)
    for index, lane in enumerate(view["lanes"]):
        y = 123 + index * row_height
        rectangle(svg, 55, y + 8, 7 + 8 * len(lane["label"]), 25, fill="#edf3f6", rx=2)
        text(svg, lane["label"], 56, y + 26, size=14, color=LINK, weight=700)
        for node in filter(None, lane["nodes"]):
            x, y_card, w, h = boxes[node["id"]]
            group = element(svg, "g", id=node["id"], data_source=node.get("ref", ""))
            fill, stroke = TONES[node.get("tone", "normal")]
            if node.get("ref"):
                group, line = sources.add(group, node["ref"])
            else:
                line = None
            rectangle(group, x, y_card, w, h, fill=fill, stroke=stroke, stroke_width=1.4)
            text(group, node["title"], x + 12, y_card + 24, size=16, weight=700)
            if "ref" in node:
                filename = node["ref"].partition("#")[0].rsplit("/", 1)[-1]
                text(group, f"{filename}:{line}" if line else filename, x + 12,
                     y_card + 43, size=12, color=LINK, family=MONO)
            detail = lines(node["detail"], int((w - 24) / 7))
            if len(detail) > 3:
                raise ValueError(f"Shorten node detail: {view['id']}/{node['id']}")
            for row, body in enumerate(detail):
                text(group, body, x + 12, y_card + 62 + row * 15, size=13, color=MUTED)
    text(svg, view.get("legend", "Solid arrow · Call or control flow  /  Dashed arrow · Async handoff or framework delegation"),
         42, height - 28, size=14, color=MUTED)
    return svg


def draw_catalog(view, sources):
    prepared = []
    for file, purpose, entries, classes in view["files"]:
        description = lines(purpose, 76)
        class_lines = sum(len(lines(item[1], 39)) for item in classes)
        height = 70 + 18 * len(description) + 20 * len(entries) + 20 * class_lines
        prepared.append((file, description, entries, classes, height))
    rows = list(zip(prepared[::2], prepared[1::2], strict=False))
    if len(prepared) % 2:
        rows.append((prepared[-1],))
    total_height = 152 + sum(max(card[4] for card in row) + 20 for row in rows)
    svg = start_svg(view, 1430, total_height)
    y = 123
    for row in rows:
        for column, (file, description, entries, classes, height) in enumerate(row):
            x = 40 + column * 685
            group = element(svg, "g", id=file.replace("/", "-"), data_source=file)
            rectangle(group, x, y, 665, height, fill="#ffffff", stroke=RULE)
            link, _ = sources.add(group, file)
            text(link, file, x + 16, y + 27, size=18, weight=700, family=MONO)
            at = y + 50
            for body in description:
                text(group, body, x + 16, at, size=15, color=MUTED)
                at += 18
            at += 7
            for index, symbol in enumerate(entries):
                ref = f"{file}#{symbol}" if symbol else file
                link, line = sources.add(group, ref)
                label = f"{symbol or 'Module declaration'}  /  line {line or 1}"
                text(link, label, x + 16, at, size=14, weight=700,
                     family=MONO, color=LINK)
                if index == 0:
                    text(group, "START HERE", x + 548, at, size=12, color=LINK, weight=700)
                at += 20
            for name, purpose, *method in classes:
                ref = f"{file}#{name}" + (f".{method[0]}" if method else "")
                link, _ = sources.add(group, ref)
                label = f"{name}.{method[0]}" if method else name
                text(link, label, x + 16, at, size=13, family=MONO, weight=650)
                description_lines = lines(purpose, 39)
                for index, body in enumerate(description_lines):
                    text(group, body, x + 382, at + 15 * index, size=13, color=MUTED)
                at += 20 * len(description_lines)
        y += max(card[4] for card in row) + 20
    return svg


def format_code_line(line, spans):
    if not spans:
        return line
    chunks = []
    cursor = 0
    for start, end, style in spans:
        if start > cursor:
            chunks.append(line[cursor:start])
        chunks.append((style, line[start:end]))
        cursor = end
    if cursor < len(line):
        chunks.append(line[cursor:])
    return chunks


def source_lines(path):
    source = path.read_text(encoding="utf-8")
    lines = source.removesuffix("\n").split("\n")
    if path.suffix != ".py":
        return lines

    spans = [[] for line in lines]
    definition_name = False
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = tokenize.tok_name[token.type]
        style = PYTHON_TOKEN_STYLES.get(kind)
        if kind == "NAME":
            if definition_name:
                style = "definition"
            elif keyword.iskeyword(token.string):
                style = "keyword"
            definition_name = token.string in {"def", "class"}
        elif kind.startswith(("FSTRING_", "TSTRING_")):
            style = "string"
        if style is None:
            continue

        first, first_column = token.start
        last, last_column = token.end
        for row in range(first, last + 1):
            start = first_column if row == first else 0
            end = last_column if row == last else len(lines[row - 1])
            if start < end:
                spans[row - 1].append((start, end, style))
    return [format_code_line(line, tokens) for line, tokens in zip(lines, spans, strict=True)]


def build_web(views, resolver, project, guide_url):
    flows = [view for view in views if "lanes" in view]
    catalogs = [view for view in views if "files" in view]
    refs = {
        node["ref"] for view in flows for lane in view["lanes"]
        for node in lane["nodes"] if node and node.get("ref")
    }
    for catalog in catalogs:
        for file, _, entries, classes in catalog["files"]:
            refs.add(file)
            refs.update(f"{file}#{symbol}" for symbol in entries if symbol)
            for name, _, *method in classes:
                refs.add(f"{file}#{name}")
                if method:
                    refs.add(f"{file}#{name}.{method[0]}")

    sources = {ref: resolver.resolve(ref) for ref in sorted(refs)}
    paths = sorted({source["file"] for source in sources.values()})
    data = {
        "project": project,
        "flows": flows,
        "catalogs": catalogs,
        "sources": sources,
        "sourceFiles": {path: source_lines(resolver.root / path) for path in paths},
    }
    assets = Path(__file__).resolve().parents[1] / "assets" / "web"
    tokens = {
        "TITLE": escape(project),
        "STYLES": (assets / "style.css").read_text(encoding="utf-8"),
        "SCRIPT": (assets / "app.js").read_text(encoding="utf-8"),
        "DATA": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", r"\u003c"),
        "GUIDE_URL": escape(guide_url),
    }
    template = (assets / "page.html").read_text(encoding="utf-8")
    return re.sub(r"@@([A-Z_]+)@@", lambda match: tokens[match[1]], template)


def main():
    parser = argparse.ArgumentParser(description="Render source-linked codebase diagrams from JSON.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--web", action="store_true", help="Generate a standalone interactive index.html.")
    parser.add_argument("--project", help="Project name in the web atlas. Defaults to the repository directory.")
    parser.add_argument("--source-base", help="Hosted base URL for links to source paths.")
    parser.add_argument("--guide-url", default="README.md", help="URL of the atlas guide.")
    parser.add_argument("spec", type=Path, nargs="+")
    args = parser.parse_args()
    sources = SourceLinks(args.root, args.out, args.source_base)
    rendered = {}
    views = [
        view for spec in args.spec for view in json.loads(spec.read_text(encoding="utf-8"))
    ]
    for view in views:
        view_id = view["id"]
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", view_id) is None or view_id in rendered:
            raise ValueError(f"Duplicate or invalid view ID: {view_id}")
        draw = draw_graph if "lanes" in view else draw_catalog
        rendered[view_id] = draw(view, sources)
    web = build_web(views, sources, args.project or sources.root.name, args.guide_url) if args.web else None
    args.out.mkdir(parents=True, exist_ok=True)
    for view_id, svg in rendered.items():
        ET.indent(svg, space="  ")
        target = args.out / f"{view_id}.svg"
        ET.ElementTree(svg).write(target, encoding="utf-8", xml_declaration=True)
        print(target)
    if web is not None:
        target = args.out / "index.html"
        target.write_text(web, encoding="utf-8")
        print(target)


if __name__ == "__main__":
    main()
