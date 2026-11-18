# V3 equipment connection evaluation

Across the six beginner PNGs, V3.3 image-only extraction scored **0.143 F1 on correctly named direct connections** and **0.296 on localized direct connections**. V3.2 scored 0.083 and 0.207, respectively. V3.3 is 0.757 below the project’s 0.90 named-connection target. The V2 and V3 runs share the same image-based symbol and text reader.

| Drawing set and measure | V2 TP / FP / FN | V2 F1 | V3 TP / FP / FN | V3 F1 |
| --- | ---: | ---: | ---: | ---: |
| NUREG, located and correctly named endpoints | 13 / 28 / 120 | 0.149 | 14 / 49 / 119 | 0.143 |
| NUREG, located endpoints | 23 / 18 / 110 | 0.264 | 29 / 34 / 104 | 0.296 |
| Earlier release, PID2Graph OPEN100, located endpoints | 0 / 35 / 125 | 0.000 | 0 / 0 / 125 | 0.000 |
| Synthetic development drawing, located and correctly named endpoints | 3 / 0 / 0 | 1.000 | 3 / 0 / 0 | 1.000 |

The detector located 83 of 125 annotated equipment symbols, up from 72. Of 126 predicted items, 37 had an unknown type, down from 135 of 137. At the 0.50 overlap threshold, 79 matched the correct equipment family (valve subtypes grouped). Of 123 printed names, it located 81 and transcribed 50 correctly, up from 30. V3 recovered 29 localized pairs and proposed 34 extra pairs. The annotations are provisional. Direction was tested separately with constructed drawings that exercise terminal tips, branches, line breaks and conflicting arrows. The three-sheet OPEN100 row records the earlier evaluation. [Beginner results](../reports/v3_comparison.json) · [Synthetic results](../reports/v3_synthetic_comparison.json) · [Earlier OPEN100 results](../reports/v3_pid2graph_comparison.json)

The optional **Read with symbol reference** workflow uses the checked geometry on an exact image match. On the same six figures it placed all 125 reference symbols and supplied their source types. Local OCR matched 99 of the 123 printed names, left 18 unread and returned 6 different readings. Two additional symbols have no printed name. The image-only scores above exclude this workflow.

## Methods and ground truth

Timings for the previous V3.2 reader on the demo computer ranged from 2.57 to 6.99 seconds per beginner page. V3.3 performs extra OCR on nearby text when the initial pass leaves names unread. Reopening a saved trace with the same reader and PNG uses the cache.

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

1. The six beginner PNGs ship in `data/input/beginner`. The team rendered them with `pdftoppm` 26.07.0 using a 3,600-pixel long side. The manifest pins the SHA-256 of each supplied image.

   ```powershell
   $beginnerImages = 'data\input\beginner'
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
