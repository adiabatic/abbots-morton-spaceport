import cProfile, pstats, io, sys, dataclasses
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(ROOT / "bench-the-rebuild/levers"))
from m1_slice import SUBSETS
from rebuild.pipeline import conform, table as table_module
from rebuild.pipeline.spec_load import load_default_spec
spec = load_default_spec()
names = SUBSETS[sys.argv[2] if len(sys.argv)>2 else "s8"]
if names: spec = dataclasses.replace(spec, runes={k:v for k,v in spec.runes.items() if k in names})
features = conform.features_for_config("default")
pr = cProfile.Profile(); pr.enable()
table_module.build_tables(spec, features, trace_store=None, share=None)
pr.disable()
s = io.StringIO(); ps = pstats.Stats(pr, stream=s); ps.sort_stats("tottime").print_stats(28)
out = s.getvalue().replace(sys.argv[1]+"/", "").replace(str(ROOT)+"/", "")
print(out)
