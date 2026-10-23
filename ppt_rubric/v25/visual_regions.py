"""Local views of possible text intersections, never automatic visual verdicts."""
from pathlib import Path
from ..common import digest, file_digest


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


def focus_views(deck, geometry, config):
    """Supplement every original slide with a bounded number of enlarged PDF crops.

    No original page is removed. Crops are keyed by the rendered PDF and region;
    their labels preserve original page numbers and describe selection limits.
    """
    if not deck.get('pdf'):return []
    available=max(0,config.get('max_images_per_request',60)-len(deck['slides']))
    limit=min(config.get('max_visual_focus_images',12),available)
    if limit<=0:return []
    regions=[r for page in geometry for r in page.get('text_overlap_candidates',[])]
    regions.sort(key=lambda r:(-r['fraction_of_smaller_bbox'],r['page'],r['id']))
    import fitz
    pdf_hash=file_digest(deck['pdf'])
    cache=Path(deck['pdf']).parent/'visual-focus-v1';cache.mkdir(exist_ok=True)
    images=[]
    with fitz.open(deck['pdf']) as pdf:
        for region in regions[:limit]:
            page=pdf[region['page']-1]
            rect=fitz.Rect(region['union'])
            padding=max(8,min(rect.width,rect.height)*0.2)
            rect=(rect+(-padding,-padding,padding,padding)) & page.rect
            scale=min(4,900/max(rect.width,rect.height))
            key=digest({'pdf':pdf_hash,'region':region,'rect':list(rect),'scale':scale})
            path=cache/(key+'.png')
            if not path.exists():
                page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False).save(path)
            label=(f"Slide {region['page']} local enlarged view {region['id']}; text spans "
                   f"{region['span_ids']}: {region['texts']}. Original PDF rectangle {list(rect)}. "
                   'A candidate bounding-box intersection, not a confirmed defect or a new slide.')
            images.append({'id': region['id'], 'page': region['page'], 'rect': list(rect),
                           'label': label, 'image': str(path)})
    return images


def focus_images(deck, geometry, config):
    return [(v['label'], v['image']) for v in focus_views(deck, geometry, config)]


def image_region_evidence(deck, geometry, config):
    """Register supplied image references, without asserting any defect."""
    return [{'id': v['id'], 'page': v['page'], 'quote': v['label'],
             'location': f"PDF rectangle {v['rect']}",
             'statement': 'Supplied image region; bounding-box intersection does not establish a defect.',
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
    from .. import prompts
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
