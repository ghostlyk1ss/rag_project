"""Add mode + outline tags to sources_index.json without Qdrant connection needed.
Called from refresh_sources_cache to ensure tags survive refreshes."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"

PRO_INDEX = DATA_DIR / "sources_index_pro.json"
SAFE_INDEX = DATA_DIR / "sources_index_safe.json"
MAIN_INDEX = DATA_DIR / "sources_index.json"

def annotate_sources_index():
    """Read pro and safe sources files to determine which docs are in which mode
    and whether they have outlines. Update the main sources_index.json with tags."""
    
    # Load pro and safe indexes to get per-mode document lists + outline info
    pro_docs = {}
    safe_docs = {}
    
    for idx_path, target in [(PRO_INDEX, pro_docs), (SAFE_INDEX, safe_docs)]:
        if not idx_path.exists():
            continue
        with open(idx_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for doc in data.get('documents', []):
            did = doc.get('doc_id', '')
            target[did] = {
                'has_outline': doc.get('has_outline', False),
                'chunks': doc.get('chunks', 0),
            }
    
    # Load main index and annotate
    if not MAIN_INDEX.exists():
        print(f"skip: {MAIN_INDEX} not found")
        return
    
    with open(MAIN_INDEX, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    updated = 0
    for doc in data.get('documents', []):
        did = doc.get('doc_id', '')
        modes = []
        if did in pro_docs:
            modes.append('pro')
        if did in safe_docs:
            modes.append('safe')
        if modes:
            doc['modes'] = modes
        # has_outline: true if either collection has outlines for this doc
        has_outline = pro_docs.get(did, {}).get('has_outline', False) or safe_docs.get(did, {}).get('has_outline', False)
        if has_outline:
            doc['has_outline'] = True
        updated += 1
    
    with open(MAIN_INDEX, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"annotated {updated} docs in sources_index.json")

if __name__ == '__main__':
    annotate_sources_index()
