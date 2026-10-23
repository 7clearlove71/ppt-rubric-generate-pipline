"""Neutral local views and an explicit inventory of visual inspection coverage.

Geometry chooses where to look. It never decides visibility, axis semantics,
materiality, or a score. Every supplied crop keeps its original-page identity.
"""
from collections import Counter
from pathlib import Path
import re
from .common import digest, file_digest


FOCUS_VERSION = 2
INSPECTION_KINDS = ('page_edge', 'overlap', 'axis_encoding', 'footer_last_line')


def overlap_regions(page):
    regions=[]
    spans=page['spans']
    for index,a in enumerate(spans):
        for b in spans[index+1:]:
            ax,ay,ar,ab=a['bbox'];bx,by,br,bb=b['bbox']
            left,top,right,bottom=max(ax,bx),max(ay,by),min(ar,br),min(ab,bb)
            if right<=left or bottom<=top:
                continue
            area=(right-left)*(bottom-top)
            smaller=min((ar-ax)*(ab-ay),(br-bx)*(bb-by))
            if smaller<=0:continue
            # These are inspection candidates, not a readability threshold.
            region={'page':page['page'],'span_ids':[a['id'],b['id']],
                    'texts':[a['text'],b['text']], 'intersection':[left,top,right,bottom],
                    'union':[min(ax,bx),min(ay,by),max(ar,br),max(ab,bb)],
                    'fraction_of_smaller_bbox':round(area/smaller,4)}
            region['id']=f"p{page['page']}_overlap_{digest(region)[:10]}"
            regions.append(region)
    return sorted(regions,key=lambda r:(-r['fraction_of_smaller_bbox'],r['span_ids']))


def _union(spans):
    return [min(s['bbox'][0] for s in spans), min(s['bbox'][1] for s in spans),
            max(s['bbox'][2] for s in spans), max(s['bbox'][3] for s in spans)]


def inspection_regions(page):
    """Locate intersections, page boundaries, final rows, and possible axis labels.

    Edge and footer bands also cover pages with no extractable text. Aligned
    labels can be a table, a categorical axis, or a continuous axis: that choice
    deliberately remains unresolved until inspection of the original image.
    """
    spans = page.get('spans', [])
    width, height = page['page_size']
    regions = []

    def add(kind, rect, members=(), priority=4, signal='scheduled_inspection', **extra):
        row = {'page': page['page'], 'kind': kind, 'union': list(rect),
               'span_ids': [s['id'] for s in members], 'texts': [s['text'] for s in members],
               'priority': priority, 'signal': signal, **extra}
        row['id'] = f"p{page['page']}_{kind}_{digest(row)[:10]}"
        regions.append(row)

    overlaps = page['text_overlap_candidates'] if 'text_overlap_candidates' in page else overlap_regions(page)
    for row in overlaps:
        regions.append({**row, 'kind': 'overlap', 'priority': 1,
                        'signal': 'bounding_box_intersection'})

    margin_x, margin_y = max(8, width * .025), max(8, height * .025)
    bands = {'left': [0, 0, margin_x * 3, height],
             'right': [width - margin_x * 3, 0, width, height],
             'top': [0, 0, width, margin_y * 3],
             'bottom': [0, height - margin_y * 3, width, height]}
    for side, band in bands.items():
        near = [s for s in spans if {'left': s['bbox'][0] <= margin_x,
                'right': s['bbox'][2] >= width - margin_x,
                'top': s['bbox'][1] <= margin_y,
                'bottom': s['bbox'][3] >= height - margin_y}[side]]
        outside = [s for s in near if s['bbox'][0] < 0 or s['bbox'][1] < 0
                   or s['bbox'][2] > width or s['bbox'][3] > height]
        # Keep the real PDF bbox even when text is completely outside the page.
        # The crop itself remains on the page and cannot reveal invisible text.
        add('page_edge', _union(near) if near else band, near,
            priority=0 if outside else 4 if near else 6,
            signal='bbox_outside_page' if outside else 'near_page_boundary' if near else 'page_boundary_band',
            side=side, boundary_band=band,
            outside_span_ids=[s['id'] for s in outside])

    visible = [s for s in spans if s['bbox'][2] > 0 and s['bbox'][0] < width
               and s['bbox'][3] > 0 and s['bbox'][1] < height]
    if visible:
        lowest = max(min(height, s['bbox'][3]) for s in visible)
        final_rows = [s for s in visible if s['bbox'][3] >= lowest - max(18, height * .075)]
        rect = _union(final_rows)
        add('footer_last_line', rect, final_rows, priority=3,
            signal='lowest_text_rows',
            inspection='Inspect the final visible rows and footnotes; do not infer clipping from extraction.')
    else:
        add('footer_last_line', [0, height * .82, width, height],
            signal='footer_band_without_text')

    # A label row is only a search hint. Retain word labels as normal category
    # controls as well as numeric/time labels; never diagnose spacing here.
    rows = []
    for span in sorted(visible, key=lambda s: (s['bbox'][1], s['bbox'][0])):
        if len(span['text'].strip()) > 36 or span['bbox'][1] < height * .2:
            continue
        center = (span['bbox'][1] + span['bbox'][3]) / 2
        tolerance = max(2, min(5, (span['bbox'][3] - span['bbox'][1]) * .25))
        row = next((r for r in rows if abs(r['center'] - center) <= tolerance), None)
        if row is None:
            rows.append({'center': center, 'spans': [span]})
        else:
            row['spans'].append(span)
    aligned_rows = []
    for row in rows:
        labels = sorted(row['spans'], key=lambda s: s['bbox'][0])
        if len(labels) < 2:
            continue
        # Split nearby fragments of a single sentence before testing coverage.
        separated = [labels[0]]
        for span in labels[1:]:
            if span['bbox'][0] - separated[-1]['bbox'][2] >= max(4, width * .006):
                separated.append(span)
        if len(separated) < 2:
            continue
        rect = _union(separated)
        if rect[2] - rect[0] < width * .18:
            continue
        aligned_rows.append({'center': row['center'], 'spans': separated})
        axis_labels = [s for s in separated if len(s['text'].strip()) <= 18]
        if len(axis_labels) < 3:
            continue
        numeric_labels = sum(bool(re.fullmatch(r'[-+−]?\d+(?:[.,]\d+)?(?:[%％]|年|月|天|小时|h)?',
                                               s['text'].strip().replace(' ', ''))) for s in axis_labels)
        rect = _union(axis_labels)
        add('axis_encoding', [max(0, rect[0] - width * .04),
                              max(0, rect[1] - height * .45),
                              min(width, rect[2] + width * .04),
                              min(height, rect[3] + height * .06)],
            axis_labels, priority=2 if numeric_labels >= 3 else 4,
            signal='aligned_separated_labels', axis_mode='unresolved', numeric_label_count=numeric_labels,
            inspection='Check whether labels are categorical or continuous, and whether values match plotted positions. Equal category spacing is valid.')

    # Repeated column starts locate possible tables even when the final row is
    # above a page footnote. A grid-like alignment is not a table verdict.
    chains = []
    for row in aligned_rows:
        chain = next((c for c in chains if 0 < row['center'] - c[-1]['center'] <= height * .13
                      and sum(any(abs(a['bbox'][0] - b['bbox'][0]) <= 6
                                  for b in c[-1]['spans']) for a in row['spans']) >= 2), None)
        if chain is None:
            chains.append([row])
        else:
            chain.append(row)
    for chain in chains:
        if len(chain) >= 3:
            final = [s for row in chain[-2:] for s in row['spans']]
            add('footer_last_line', _union(final), final, priority=3,
                signal='aligned_table_final_rows',
                inspection='Inspect the last aligned rows and their complete values/units against the original page. Column alignment alone does not establish a table or clipping.')
    return sorted(regions, key=lambda r: (r['priority'], r['kind'], r['id']))


def _available_regions(geometry):
    return [region for page in geometry for region in
            page.get('inspection_candidates', page.get('text_overlap_candidates', []))]


def _selected_regions(regions, limit):
    """Deterministically spread the budget across pages and inspection domains."""
    selected, remaining = [], list(regions)
    page_counts, kind_counts = Counter(), Counter()
    while remaining and len(selected) < limit:
        region = min(remaining, key=lambda r: (
            page_counts[r['page']], kind_counts[(r['page'], r.get('kind', 'overlap'))],
            r.get('priority', 1), -r.get('numeric_label_count', 0),
            -r.get('fraction_of_smaller_bbox', 0), r['page'], r['id']))
        remaining.remove(region)
        selected.append(region)
        page_counts[region['page']] += 1
        kind_counts[(region['page'], region.get('kind', 'overlap'))] += 1
    return selected


def focus_coverage(deck, geometry, config, views):
    """Record image supply and omissions, without claiming model inspection.

    In particular, an uncropped candidate is not an unobserved page: every
    original page is still supplied. Conversely, supplying an image does not
    prove that an assessor checked every object in it.
    """
    numbers = {s['page'] for s in deck['slides']}
    regions = [r for r in _available_regions(geometry) if r['page'] in numbers]
    selected = {v['id'] for v in views}
    return {'version': FOCUS_VERSION, 'policy': 'original pages plus bounded neutral risk crops',
            'automatic_verdict': False, 'model_inspection_status': 'not_established_by_image_supply',
            'max_focus_images': config.get('max_visual_focus_images', 12),
            'full_page_numbers': sorted(numbers), 'available_candidates': len(regions),
            'selected_crop_ids': [v['id'] for v in views],
            'uncropped_candidate_ids': [r['id'] for r in regions if r['id'] not in selected],
            'pages': [{'page': p,
                       'candidate_counts': dict(Counter(r.get('kind', 'overlap') for r in regions if r['page'] == p)),
                       'selected_crop_ids': [v['id'] for v in views if v['page'] == p],
                       'uncropped_candidate_ids': [r['id'] for r in regions if r['page'] == p and r['id'] not in selected]}
                      for p in sorted(numbers)]}


def focus_views(deck, geometry, config):
    """Supplement every original slide with a bounded number of enlarged PDF crops.

    No original page is removed. Crops are keyed by the rendered PDF and region;
    their labels preserve original page numbers and describe selection limits.
    """
    if not deck.get('pdf'):return []
    available=max(0,config.get('max_images_per_request',60)-len(deck['slides']))
    limit=min(config.get('max_visual_focus_images',12),available)
    if limit<=0:return []
    page_numbers = {s['page'] for s in deck['slides']}
    regions=[r for r in _available_regions(geometry) if r['page'] in page_numbers]
    import fitz
    pdf_hash=file_digest(deck['pdf'])
    cache=Path(deck['pdf']).parent/f'visual-focus-v{FOCUS_VERSION}';cache.mkdir(exist_ok=True)
    images=[]
    with fitz.open(deck['pdf']) as pdf:
        for region in _selected_regions(regions, limit):
            page=pdf[region['page']-1]
            rect=fitz.Rect(region['union'])
            padding=max(8,min(rect.width,rect.height)*0.2)
            rect=(rect+(-padding,-padding,padding,padding)) & page.rect
            if rect.is_empty:
                # Fully out-of-page text cannot be rendered into an image.
                # Show the relevant boundary, retaining its off-page source bbox.
                rect = fitz.Rect(region.get('boundary_band', list(page.rect))) & page.rect
            if rect.is_empty:
                continue
            scale=min(4,config.get('visual_focus_max_side',1800)/max(rect.width,rect.height))
            key=digest({'pdf':pdf_hash,'region':region,'rect':list(rect),'scale':scale})
            path=cache/(key+'.png')
            if not path.exists():
                page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False).save(path)
            kind = region.get('kind', 'overlap')
            label=(f"Slide {region['page']} local enlarged view {region['id']}; inspection domain {kind}; text spans "
                   f"{region['span_ids']}: {region['texts']}. Original PDF rectangle {list(rect)}. "
                   'A neutral localization candidate, not a confirmed defect, visibility finding, or a new slide. '
                   'Geometry/extracted text may extend outside the image; judge the supplied original page. '
                   + region.get('inspection', ''))
            images.append({'id': region['id'], 'page': region['page'], 'rect': list(rect),
                           'label': label, 'image': str(path), 'kind': kind,
                           'signal': region.get('signal', 'bounding_box_intersection'),
                           'span_ids': region.get('span_ids', []), 'source_rect': region['union']})
    return images


def focus_images(deck, geometry, config):
    return [(v['label'], v['image']) for v in focus_views(deck, geometry, config)]


def image_region_evidence(deck, geometry, config):
    """Register supplied image references, without asserting any defect."""
    return [{'id': v['id'], 'page': v['page'], 'quote': v['label'],
             'location': f"PDF rectangle {v['rect']}",
             'statement': 'Supplied image region; geometry and localization signals do not establish a defect.',
             'kind': 'visual', 'failure_mode': None, 'failure_mode_origin': 'image_region',
             'source_refs': [], 'quantities': [], 'calculations': [], 'arithmetic_checks': []}
            for v in focus_views(deck, geometry, config)]


def geometry_reference_evidence(geometry):
    """Neutral references already supplied alongside every original page image.

    A geometry region need not have a separate enlarged crop to be a valid
    reference to that original image. Neither a region nor a span proves a defect.
    """
    rows=[]
    for page in geometry:
        for span in page.get('spans', []):
            rows.append({'id':span['id'], 'page':page['page'], 'quote':span['text'],
                         'location':f"PDF text rectangle {span['bbox']}"})
        for region in page.get('text_overlap_candidates', []):
            rows.append({'id':region['id'], 'page':page['page'],
                         'quote':' / '.join(region['texts']),
                         'location':f"PDF candidate rectangle {region['union']}"})
    return [{**row, 'kind':'visual', 'failure_mode':None, 'failure_mode_origin':'image_region',
             'statement':'Supplied original-page geometry reference; not an independent inspection or confirmed defect.',
             'source_refs':[], 'quantities':[], 'calculations':[], 'arithmetic_checks':[]}
            for row in rows]


def validate_visual_inspection(result):
    if not isinstance(result, dict):
        raise ValueError('Visual inspection must be an object')
    if result.get('overlapping') is not None and type(result.get('overlapping')) is not bool:
        raise ValueError('overlapping must be true, false or null')
    if 'overlapping' not in result:
        raise ValueError('Visual inspection must state overlapping')
    for key in ('text_seen', 'overlap_objects'):
        if not isinstance(result.get(key), list) or any(not isinstance(s, str) or not s.strip() for s in result[key]):
            raise ValueError(f'Visual inspection needs {key} as a text list')
    if result['overlapping'] is True and not result['overlap_objects']:
        raise ValueError('Observed overlap must identify visible objects')
    if result['overlapping'] is False and result['overlap_objects']:
        raise ValueError('Absent overlap cannot list overlapping objects')
    if not isinstance(result.get('description'), str) or not result['description'].strip():
        raise ValueError('Visual inspection requires a visible description')


def collect_visual_inspections(deck, geometry, client, config):
    """Read selected crops without the rubric, extracted text, or previous claims.

    These observations are fallible and never assign a score or defect owner.
    The full-page judge must explicitly resolve them against its criterion.
    """
    from . import prompts
    limit = config.get('max_visual_inspections', 2)
    if not limit:
        return []
    records = []
    for view in focus_views(deck, geometry, config)[:limit]:
        record = {k: view[k] for k in ('id', 'page', 'rect', 'image')}
        try:
            result = client.ask('judge', prompts.VISUAL_INSPECTION, {},
                                [('Image', view['image'])], validate=validate_visual_inspection)
            validate_visual_inspection(result)
            record.update(status='observed', observation=result)
        except Exception as e:
            record.update(status='unavailable', error_type=type(e).__name__)
        records.append(record)
    return records
