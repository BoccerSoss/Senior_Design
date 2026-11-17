# V3 equipment connection evaluation

V2 and V3 both scored **0.000 F1 on named connections** across six NUREG/CR-5745 beginner drawings, a gap of 0.900 from the 0.90 target. Each method also scored **0.000 F1 on localized connections** in a separate three-sheet pilot using independently published PID2Graph annotations. PID2Graph does not supply equipment-name truth for this comparison.

| Drawing set and measure | V2 TP / FP / FN | V2 F1 | V3 TP / FP / FN | V3 F1 |
| --- | ---: | ---: | ---: | ---: |
| NUREG, located and correctly named endpoints | 0 / 137 / 133 | 0.000 | 0 / 60 / 133 | 0.000 |
| NUREG, located endpoints | 7 / 130 / 126 | 0.052 | 6 / 54 / 127 | 0.062 |
| PID2Graph OPEN100, located endpoints | 0 / 35 / 125 | 0.000 | 0 / 0 / 125 | 0.000 |
| Synthetic development drawing, located and correctly named endpoints | 3 / 0 / 0 | 1.000 | 3 / 0 / 0 | 1.000 |

On the NUREG drawings, V3 increased localized-edge precision from 5.1% to 10.0% and decreased recall from 5.3% to 4.5%. The shared detector located 14 of 125 equipment symbols. Only seven reference pairs had both endpoints located, and none had both names correct. On OPEN100 it located six of 219 symbols, leaving zero reference pairs with both endpoints available. Recovering additional connections on these sheets requires improving equipment detection. All ten drawing comparisons completed. Counts, endpoint coverage, per-sheet metrics, input hashes and method identifiers are in the [NUREG report](../reports/v3_comparison.json), [OPEN100 report](../reports/v3_pid2graph_comparison.json) and [synthetic report](../reports/v3_synthetic_comparison.json).

## Methods and ground truth

- **V2** runs the existing Windows OCR, shape classifier and geometry-based connection extraction. The benchmark disables V2's exact-drawing reference lookup. That lookup supplies some of the same geometry used by the NUREG evaluator.
- **V3 local** takes the same image-derived component boxes and OCR readings. It proposes straight and single-elbow connections at detected symbol contacts, then classifies line continuity. Its nearest-centroid model used 960 procedurally generated stroke profiles and seed 340197. Each method receives the same metadata-stripped PNG pixels. Neither receives reference nodes, transcriptions, edges or user corrections. The measured model is `synthetic-stroke-centroids-v1`.
- **NUREG/CR-5745** uses complete figures A.1–A.6, PDF pages 65–70. Reference geometry and evaluator-only printed-name transcriptions predate this experiment. These related figures were used during prototype development. Independent human review of the annotations is pending. [Source report](https://www.osti.gov/servlets/purl/10145659) · [Evaluation manifest and transcriptions](../data/evaluation/nureg-cr5745-beginner.json)
- **PID2Graph OPEN100** uses independently published GraphML. The three complete sheets numbered 0, 1 and 2 were selected in numerical order before scoring. Nine other sheets in this collection were reserved. The converter derives undirected equipment pairs along solid edges. It walks through degree-two line marks and stops at branches, sheet boundaries and intervening equipment. The source annotations do not establish printed equipment names or distinguish process from signal on solid lines. The score measures localized solid-line adjacency under this convention. [PID2Graph v1 data](https://zenodo.org/records/14803338) · [Authors' paper](https://arxiv.org/abs/2411.13929)
- **Synthetic** uses the project's known four-component development drawing and computer-generated reference. The 3/3 result checks that the complete workflow can recover a simple chain. [Synthetic reference](../data/reference/four_component.reference.json)

## Scoring

A positive is one direct, undirected equipment pair on a single sheet, using the same equipment, edge-kind and duplicate handling as the displayed matrix. Symbol boxes are matched one-to-one, in descending intersection-over-union with an inclusive threshold of 0.50. Ties are broken by observation IDs. Neither tags nor predicted edges influence box matching.

For **localized edges**, a true positive requires both endpoint boxes to match the expected pair. For **named edges**, each matched endpoint must also have the expected normalized printed name. A reference with an explicitly unprinted name requires an empty predicted name at that location. If name annotations are unavailable, the named metric is unavailable for that sheet and its cohort. Direction and equipment class are outside these two measures.

Every extra predicted matrix pair counts as a false positive, including a pair with an unmatched endpoint. Every missing reference pair counts as a false negative. Wrong endpoint names can therefore produce both errors. The evaluator sums counts across whole sheets before calculating precision, recall and F1. A method that predicts zero edges on a sheet with true edges has recall and F1 of zero and undefined precision. When both edge sets are empty, F1 is undefined. An incomplete cohort produces a null full-cohort metric for the affected method. The reported signed gap is 0.90 minus F1.

## Reproduce on Windows

Use the project virtual environment with Python 3.12, the dependencies in `requirements-v2.lock`, and Windows OCR. The reported runs used Windows.Media.Ocr in en-US on Windows build 10.0.26100.

1. For NUREG, put the six original beginner PNGs in a folder. The team rendered them with `pdftoppm` 26.07.0 using a 3,600-pixel long side. The manifest pins the SHA-256 of each supplied image. Replace the first path below with the PNG folder location.

   ```powershell
   $beginnerImages = 'C:\drawing-data\01_Beginner'
   .\.venv\Scripts\python.exe scripts\compare_v3.py --images "$beginnerImages" --report runs\nureg-comparison.json
   ```

2. For the independent comparison, extract the OPEN100 complete-plan folder from PID2Graph v1 and point the scripts at it. The converter reads the three PNG/GraphML pairs. The model sees only the PNG.

   ```powershell
   $open100 = 'C:\drawing-data\PID2Graph\Complete\PID2Graph OPEN100'
   .\.venv\Scripts\python.exe scripts\prepare_pid2graph.py "$open100" runs\open100-reference
   .\.venv\Scripts\python.exe scripts\compare_v3.py --images "$open100" --manifest runs\open100-reference\manifest.json --report runs\open100-comparison.json
   ```

3. The synthetic control uses the drawing and evaluator reference already in the repository.

   ```powershell
   .\.venv\Scripts\python.exe scripts\compare_v3.py --images data\input --manifest data\evaluation\synthetic-demo.json --report runs\synthetic-comparison.json
   ```

The benchmark creates a new directory under `runs/v3-benchmark/` for every invocation. It saves image-only predictions, matched nodes, extra predicted pairs, missed reference pairs and per-stage failures. Portable reports use SHA-256 of the original and prepared PNG bytes. JSON manifest and annotation fingerprints use UTF-8, sorted keys and compact separators for cross-platform reproducibility.

To evaluate an approved remote vision model, add `--method vision-api --provider openai --model MODEL_ID` or `--method vision-api --provider anthropic --model MODEL_ID` to a benchmark command and choose a new report path. These modes send the drawing images to the configured service. The benchmark results above cover local V3 inference only.
