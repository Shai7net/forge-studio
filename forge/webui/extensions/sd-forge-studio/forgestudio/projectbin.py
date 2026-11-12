"""Only files explicitly registered with this project, independently of the global index."""
import os
from urllib.parse import urlencode
from . import library, projects


def listed(name, kind='', text='', offset=0, limit=90):
    studio = projects._studio(projects._folder(name))
    items = []
    for web, item in studio['media'].items():
        path = item['path']
        # Explicitly imported references remain visible even if the global scanner calls
        # them helper files (for example a Video Studio .first.png).
        media_kind = library._KIND_OF.get(os.path.splitext(path)[1].lower())
        if not media_kind or kind and kind != media_kind or not os.path.isfile(path):
            continue
        if text and text.casefold() not in (web + ' ' + path).casefold():
            continue
        try:
            mtime=os.path.getmtime(path)
        except OSError:
            continue
        query = urlencode({'name': name, 'web': web})
        items.append({'web': web, 'project': name, 'name': web, 'path': path, 'kind': media_kind,
                      'mtime': item.get('added', mtime),
                      'url': '/forge-studio/project/media?' + query,
                      'thumb': '/forge-studio/project/thumb?' + query})
    items.sort(key=lambda i: (-i['mtime'], i['web']))
    offset, limit = max(0, int(offset)), max(1, min(200, int(limit)))
    return {'items': items[offset:offset + limit], 'total': len(items)}
