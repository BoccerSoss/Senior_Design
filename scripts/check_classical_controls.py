"""Preserve image-based retagging/erased-label controls after a classical demo.

Control expectations live only in this evaluator-side script. Each extractor
receives a new two-file raster/prompt packet, never these assertions or truth.
"""
import argparse
import json
import sys
from pathlib import Path
from PIL import ImageDraw, ImageFont
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from aip.io import image_info, load_json, parse_response, prepare_input, write_json
from aip.cli import process_run

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--demo",type=Path,required=True)
parser.add_argument("--out",type=Path,required=True)
args=parser.parse_args()
out=args.out.resolve()
out.mkdir(parents=True,exist_ok=False)
summary=load_json(args.demo/"summary.json")
if summary.get("method")!="classical" or not summary["control_checks_pass"]:
    raise SystemExit("Supply a successful classical demo directory.")
original_run=Path(summary["runs"]["clean"]["run"])
raw=parse_response(original_run/"raw_response.txt")
associations=raw["method_evidence"]["associations"]
tagged=[x for x in associations if x["observed_tag"] is not None and x["source_word_bbox"] is not None]
if not tagged:
    raise SystemExit("Clean extraction has no observed tag locations; controls cannot be claimed.")
image=image_info(args.demo/"packets/clean/image.png")
results={}
for name in ("retagged","labels_erased"):
    control=image.copy()
    draw=ImageDraw.Draw(control)
    targets=tagged[:1] if name=="retagged" else tagged
    for item in targets:
        x0,y0,x1,y1=item["source_word_bbox"]
        draw.rectangle((x0-9,y0-9,x1+9,y1+9),fill="white")
    expected_new="ZX-742"
    if name=="retagged":
        x0,y0,x1,y1=targets[0]["source_word_bbox"]
        font=ImageFont.truetype(str(ROOT/"output/aip_four_component/source/fonts/VeraBd.ttf"),max(16,round((y1-y0)*1.4)))
        draw.text(((x0+x1)/2,y1+1),expected_new,font=font,fill="black",anchor="ms")
    filename=out/(name+".png")
    control.save(filename)
    packet=out/"packets"/name
    prepare_input(filename,packet)
    run,code=process_run(packet,out/"runs",method="classical")
    if code:
        raise SystemExit(f"{name} extraction failed; preserved {run}")
    doc=load_json(run/"prediction.json")
    observed=[c["normalized_tag"] for c in doc["components"]]
    if name=="retagged":
        passed=expected_new in observed and targets[0]["observed_tag"].upper() not in observed
        expected={"new_tag":expected_new,"old_observed_tag_absent":targets[0]["observed_tag"]}
    else:
        passed=bool(doc["components"]) and all(t is None for t in observed)
        expected={"all_candidate_tags":None}
    results[name]={"passed":passed,"expectation":expected,"observed_tags":observed,
        "candidate_count":len(doc["components"]),"contact_count":len(doc["connections"]),
        "observed_types":[c["type"] for c in doc["components"]],"run":str(run),
        "clean_reference_scoring":"not performed: changed pixels do not match clean reference SHA"}
report={"all_pass":all(x["passed"] for x in results.values()),"controls":results,
        "scope":"development robustness controls, not held-out general accuracy or human verification"}
write_json(out/"summary.json",report)
print(json.dumps(report,indent=2))
raise SystemExit(0 if report["all_pass"] else 2)
