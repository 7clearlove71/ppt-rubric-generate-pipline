from __future__ import annotations

import posixpath
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .common import digest, file_digest, read_json, write_json

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
RENDER_VERSION = 2


def font_environment(config, cache_root):
    """Configure font discovery only for the conversion subprocess; no system changes."""
    dirs = [Path(p).expanduser().resolve() for p in config.get('font_dirs', []) if Path(p).expanduser().is_dir()]
    if not dirs:
        return os.environ.copy(), None
    inventory = []
    for directory in dirs:
        for p in sorted(directory.rglob('*')):
            if p.suffix.lower() in {'.ttf', '.ttc', '.otf'} and p.is_file():
                stat = p.stat()
                inventory.append((str(p), stat.st_size, stat.st_mtime_ns))
    fingerprint = digest(inventory)
    folder = Path(cache_root).resolve() / 'fontconfig' / fingerprint
    folder.mkdir(parents=True, exist_ok=True)
    root = ET.Element('fontconfig')
    for directory in dirs:
        ET.SubElement(root, 'dir').text = str(directory)
    ET.SubElement(root, 'cachedir').text = str(folder / 'cache')
    # Preserve installed fonts; sensible CJK fallback when a Windows font is absent.
    for family, preferred in [('sans-serif', ['Arial', 'Heiti SC']), ('serif', ['Times New Roman', 'Songti SC']),
                              ('Microsoft YaHei', ['Microsoft YaHei', 'Heiti SC']), ('微软雅黑', ['Microsoft YaHei', 'Heiti SC'])]:
        alias = ET.SubElement(root, 'alias')
        ET.SubElement(alias, 'family').text = family
        prefer = ET.SubElement(alias, 'prefer')
        for name in preferred:
            ET.SubElement(prefer, 'family').text = name
    path = folder / 'fonts.conf'
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.fonts-', delete=False) as f:
            ET.ElementTree(root).write(f, encoding='utf-8', xml_declaration=True)
            temporary = f.name
        os.replace(temporary, path)
    env = os.environ.copy()
    env['FONTCONFIG_FILE'] = str(path)
    return env, fingerprint


def extract_pptx(path):
    """Use presentation relationship order, not numeric slide filenames. Exclude notes."""
    with zipfile.ZipFile(path) as z:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        rels = ET.fromstring(z.read("ppt/_rels/presentation.xml.rels"))
        targets = {r.attrib["Id"]: posixpath.normpath(posixpath.join("ppt", r.attrib["Target"]))
                   if not r.attrib["Target"].startswith('/') else r.attrib["Target"].lstrip('/')
                   for r in rels if r.attrib.get("TargetMode") != "External"}
        slides = []
        for i, node in enumerate(pres.findall("p:sldIdLst/p:sldId", NS), 1):
            target = targets[node.attrib['{' + NS['r'] + '}id']]
            root = ET.fromstring(z.read(target))
            paras = [''.join(t.text or '' for t in p.findall('.//a:t', NS)) for p in root.findall('.//a:p', NS)]
            slides.append({"page": i, "text": '\n'.join(t for t in paras if t),
                           "hidden": root.attrib.get('show') == '0'})
        return slides


def rendered_text_geometry(deck):
    """PDF text spans assist spatial checks; bounding boxes do not prove visibility."""
    if not deck.get('pdf'):
        return []
    import fitz
    from .visual_regions import overlap_regions
    pages = []
    wanted = {slide['page'] for slide in deck['slides']}
    with fitz.open(deck['pdf']) as pdf:
        for number in sorted(wanted):
            page = pdf[number - 1]
            spans = []
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if not span['text'].strip():
                            continue
                        spans.append({'id': f'p{number}_t{len(spans)+1}', 'text': span['text'],
                                      'bbox': [round(x, 2) for x in span['bbox']],
                                      'color': f'#{span["color"]:06x}'})
            pages.append({'page': number, 'page_size': [page.rect.width, page.rect.height],
                          'coordinate_system': 'PDF points; origin top left; bbox=x0,y0,x1,y1',
                          'spans': spans,
                          'limit': 'Bounding boxes assist localization only. Intersection is not proof of occlusion; inspect the image.'})
            pages[-1]['text_overlap_candidates'] = overlap_regions(pages[-1])
    return pages


def prepare_deck(pptx, config, cache_root):
    import fitz
    pptx = Path(pptx).resolve()
    configured = str(Path(config["soffice"]).expanduser())
    soffice = str(Path(shutil.which(configured) or configured).resolve())
    if not Path(soffice).exists():
        raise ValueError("Configure an available headless LibreOffice binary in 'soffice'")
    # Binary version and settings are part of the render cache key.
    version = subprocess.run([soffice, '--version'], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    render_env, font_id = font_environment(config, cache_root)
    identity = {"sha256": file_digest(pptx), "renderer": version, "soffice": soffice,
                "dpi": config['dpi'], "backend": config['pdf_backend'], "pymupdf": fitz.VersionBind,
                "render_version": RENDER_VERSION, "font_inventory": font_id}
    cache = Path(cache_root).resolve() / 'render' / digest(identity)
    saved = cache / 'deck.json'
    if saved.exists():
        deck = read_json(saved)
        if Path(deck['pdf']).exists() and all(Path(s['image']).exists() for s in deck['slides']):
            return deck
    slides = extract_pptx(pptx)
    if not slides:
        raise ValueError("Empty PPTX")
    if any(s['hidden'] for s in slides):
        raise ValueError("Hidden slides require an explicit evaluation policy; refusing page mismatch")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ppt-rubric-render-') as td:
        temp = Path(td)
        profile = temp / 'profile'
        profile.mkdir()
        # Isolated user profile follows PresentBench; never overwrite source PPTX.
        subprocess.run([soffice, f'-env:UserInstallation={profile.as_uri()}', '--headless',
                        '--convert-to', 'pdf', '--outdir', str(temp), str(pptx)],
                       check=True, capture_output=True, timeout=300, env=render_env)
        generated = temp / (pptx.stem + '.pdf')
        if not generated.exists():
            raise ValueError('LibreOffice returned without creating PDF')
        with fitz.open(generated) as pdf:
            if len(pdf) != len(slides):
                raise ValueError(f'PDF/PPTX page mismatch: {len(pdf)} vs {len(slides)}')
            for page, slide in zip(pdf, slides):
                slide['rendered_text'] = page.get_text()
        shutil.copy2(generated, cache / 'slides.pdf')
    images = []
    backend = config['pdf_backend']
    if backend == 'poppler':
        executable = config.get('pdftoppm') or shutil.which('pdftoppm')
        if not executable:
            raise ValueError('poppler selected but pdftoppm unavailable')
        subprocess.run([executable, '-png', '-r', str(config['dpi']), str(cache / 'slides.pdf'),
                        str(cache / 'page')], check=True, capture_output=True, timeout=300)
        images = sorted(cache.glob('page-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
    elif backend == 'pymupdf':
        with fitz.open(cache / 'slides.pdf') as pdf:
            for i, page in enumerate(pdf, 1):
                dest = cache / f'page-{i:03d}.png'
                page.get_pixmap(matrix=fitz.Matrix(config['dpi'] / 72, config['dpi'] / 72), alpha=False).save(dest)
                images.append(dest)
    else:
        raise ValueError('pdf_backend must be pymupdf or poppler')
    if len(images) != len(slides):
        raise ValueError('Image count mismatch')
    for slide, image in zip(slides, images):
        slide['image'] = str(image)
    deck = {"identity": identity, "pdf": str(cache / 'slides.pdf'), "slides": slides}
    write_json(saved, deck)
    return deck


def prepare_case(case, config, cache_root):
    out = {}
    for c in case['candidates']:
        if file_digest(c['pptx']) != c['sha256']:
            raise ValueError(f"Candidate changed since manifest freeze: {case['id']}/{c['id']}")
        out[c['id']] = prepare_deck(c['pptx'], config, cache_root)
    return out


def case_sources(case, config):
    """No silent attachment omission. Plain text PDFs only until visual adaptation."""
    blocks = [{"id": "prompt", "text": case['prompt']}]
    for i, attachment in enumerate(case.get('attachments', []), 1):
        path = Path(attachment['path'] if isinstance(attachment, dict) else attachment)
        source_hash = file_digest(path)
        if isinstance(attachment,dict) and attachment.get('sha256') and source_hash!=attachment['sha256']:
            raise ValueError(f'Attachment {i} changed since manifest freeze')
        if path.suffix.lower() in {'.txt', '.md', '.csv', '.json'}:
            blocks.append({"id": f"attachment_{i}", "source_sha256":source_hash,"text": path.read_text(encoding='utf-8')})
        elif path.suffix.lower() == '.pdf':
            import fitz
            with fitz.open(path) as pdf:
                for j, page in enumerate(pdf, 1):
                    if page.get_images(full=True) or page.get_drawings():
                        raise ValueError(f'Attachment {i} page {j} contains visual elements; verified visual adapter required')
                    text = page.get_text().strip()
                    if not text:
                        raise ValueError(f'Attachment {i} page {j} has no text; OCR/visual adapter required')
                    blocks.append({"id": f"attachment_{i}_page_{j}", "source_sha256":source_hash,"text": text})
        else:
            raise ValueError(f'Unsupported attachment: {path.suffix}; add a verified adapter')
    if sum(len(x['text']) for x in blocks) > config['max_case_text_chars']:
        raise ValueError('Source text exceeds budget; require chunked evidence extraction, never truncate silently')
    return blocks
