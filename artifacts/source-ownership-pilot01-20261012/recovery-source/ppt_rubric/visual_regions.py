"""Local views of possible text intersections, never automatic visual verdicts."""
from pathlib import Path
from .common import digest, file_digest


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


def focus_images(deck, geometry, config):
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
            images.append((label,str(path)))
    return images
