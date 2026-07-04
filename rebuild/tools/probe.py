"""Probe a codepoint window: old-font baseline (glyphs+seams, all configs) vs new settlement.
Usage: uv run python rebuild/tools/probe.py E653:E666:E652
"""
import sys, gzip
from pathlib import Path
from rebuild.pipeline import conform
from rebuild.pipeline.run_m1 import OUT_DIR
from rebuild.pipeline.spec_load import load_default_spec
from rebuild.pipeline import settle as settle_module

CONFIGS=['default','ss02','ss03','ss02+ss03','ss05','ss02+ss03+ss05','ss04','ss10']

def load_subset(cfg):
    rows={}
    p=OUT_DIR/f"baseline-{cfg}.subset.tsv.gz"
    with gzip.open(p,'rt') as f:
        for line in f:
            if line.startswith('#'): continue
            parts=line.rstrip('\n').split('\t')
            if len(parts)<4: continue
            rows[parts[0]]=parts
    return rows

def main():
    cps_str=sys.argv[1].upper()
    cps=[int(x,16) for x in cps_str.split(':')]
    spec=load_default_spec()
    print(f"=== window {cps_str} ===")
    for cfg in CONFIGS:
        sub=load_subset(cfg)
        feats=conform.features_for_config(cfg)
        # baseline
        b=sub.get(cps_str)
        bg = b[1] if b else "(not in subset)"
        bs = b[3] if b else ""
        # new settlement
        settled=settle_module.settle(spec, list(cps), feats)
        cells=[]
        seams=[]
        for i,it in enumerate(settled):
            c=getattr(it,'cell',None)
            if c is not None and hasattr(c,'rune'):
                cells.append(f"{c.rune}.{c.stance}/en={c.entry}/ex={c.exit}/{'+'.join(c.adjustments)}")
            else:
                cells.append(getattr(it,'glyph_name',str(it)))
            if i<len(settled)-1:
                sm=getattr(it,'seam',None)
                seams.append('break' if sm is None else (f"y{sm}" if isinstance(sm,int) else f"y{spec.registry.y_of(sm)}"))
        print(f"\n[{cfg}]")
        print(f"  OLD glyphs: {bg}")
        print(f"  OLD seams : {bs}")
        print(f"  NEW cells : {' | '.join(cells)}")
        print(f"  NEW seams : {','.join(seams)}")

if __name__=='__main__':
    main()
