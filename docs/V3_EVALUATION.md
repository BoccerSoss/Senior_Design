# V3 equipment connection evaluation

Across the six beginner PNGs, V3.6 local extraction scored **0.525 F1 on correctly named direct connections**. The optional ChatGPT label-crop workflow scored **0.619**. Both scored **0.729** on localized direct connections. V3.3 scored 0.143 and 0.296, respectively. The project target is 0.90 named-connection F1.

| Drawing set and measure | V2 TP / FP / FN | V2 F1 | V3 TP / FP / FN | V3 F1 |
| --- | ---: | ---: | ---: | ---: |
| NUREG, located and correctly named endpoints, V3 local | 23 / 31 / 110 | 0.246 | 62 / 41 / 71 | 0.525 |
| NUREG, located and correctly named endpoints, V3 with ChatGPT label crops | 23 / 31 / 110 | 0.246 | 73 / 30 / 60 | 0.619 |
| NUREG, located endpoints | 39 / 15 / 94 | 0.417 | 86 / 17 / 47 | 0.729 |
| Earlier release, PID2Graph OPEN100, located endpoints | 0 / 35 / 125 | 0.000 | 0 / 0 / 125 | 0.000 |
| Synthetic development drawing, located and correctly named endpoints | 3 / 0 / 0 | 1.000 | 3 / 0 / 0 | 1.000 |

The detector located 121 of 125 annotated equipment symbols, up from 83. At the 0.50 overlap threshold, 117 matched the annotated equipment family with valve subtypes grouped. Of 123 printed names, it transcribed 99 correctly with local OCR and 110 with ChatGPT label crops. Both endpoints were located for 128 of 133 reference connections. In the ChatGPT crop run, both endpoints were correctly named for 108 pairs. The tracer recovered 86 pairs by location and missed 47. The annotations are provisional. Direction was tested separately on terminal tips, branches, line breaks and conflicting arrows. The three-sheet OPEN100 row records the earlier evaluation. [Beginner local results](../reports/v3_comparison.json) · [ChatGPT label-crop results](../reports/v3_subscription_crop_comparison.json) · [Synthetic results](../reports/v3_synthetic_comparison.json) · [Earlier OPEN100 results](../reports/v3_pid2graph_comparison.json)

The optional **Read with symbol reference** workflow uses the checked geometry on an exact image match. On the same six figures it placed all 125 reference symbols and supplied their source types. Local OCR matched 99 of the 123 printed names, left 18 unread and returned 6 different readings. Two additional symbols have no printed name. The image-only scores above exclude this workflow.

## Methods and ground truth

Reopening a saved trace with the same reader and PNG uses the cache. A new local trace performs full-page OCR, checks nearby label crops and follows the line centerlines. The optional ChatGPT workflow adds the time needed to transcribe its crops.

- **V2** runs the existing Windows OCR, shape classifier and geometry-based connection extraction. The benchmark disables V2's exact-drawing reference lookup. That lookup supplies some of the same geometry used by the NUREG evaluator.
- **V3 local** takes the same image-derived component boxes and OCR readings. The centerline router follows connected ink through bends and tees, stops at other equipment, and uses visible arrow evidence and junction dots at branches. A traced line stays available for review when one end has neither a recognized type nor a readable name. The matrix includes it when that endpoint is identified or the line is confirmed. The straight and single-elbow stroke classifier remains available when centerline tracing produces no routes. Both methods receive the same metadata-stripped PNG pixels.
- **NUREG/CR-5745** uses complete figures A.1–A.6, PDF pages 65–70. Reference geometry and evaluator-only printed-name transcriptions predate this experiment. These related figures were used during prototype development. Independent human review of the annotations is pending. [Source report](https://www.osti.gov/servlets/purl/10145659) · [Evaluation manifest and transcriptions](../data/evaluation/nureg-cr5745-beginner.json)
- **PID2Graph OPEN100** uses independently published GraphML. The three complete sheets numbered 0, 1 and 2 were selected in numerical order before scoring. Nine other sheets in this collection were reserved. The converter derives undirected equipment pairs along solid edges. It walks through degree-two line marks and stops at branches, sheet boundaries and intervening equipment. The source annotations do not establish printed equipment names or distinguish process from signal on solid lines. The score measures localized solid-line adjacency under this convention. [PID2Graph v1 data](https://zenodo.org/records/14803338) · [Authors' paper](https://arxiv.org/abs/2411.13929)
- **Synthetic** uses the project's known four-component development drawing and computer-generated reference. The 3/3 result checks that the complete workflow can recover a simple chain. [Synthetic reference](../data/reference/four_component.reference.json)

## Scoring

A positive is one direct, undirected equipment pair on a single sheet, using the same equipment, edge-kind and duplicate handling as the displayed matrix. Symbol boxes are matched one-to-one, in descending intersection-over-union with an inclusive threshold of 0.50. Ties are broken by observation IDs. Neither tags nor predicted edges influence box matching.

For **localized edges**, a true positive requires both endpoint boxes to match the expected pair. For **named edges**, each matched endpoint must also have the expected normalized printed name. A reference with an explicitly unprinted name requires an empty predicted name at that location. If name annotations are unavailable, the named metric is unavailable for that sheet and its cohort. Direction and equipment class are outside these two measures.

Every extra predicted matrix pair counts as a false positive, including a pair with an unmatched endpoint. Every missing reference pair counts as a false negative. Wrong endpoint names can therefore produce both errors. The evaluator sums counts across whole sheets before calculating precision, recall and F1. A method that predicts zero edges on a sheet with true edges has recall and F1 of zero and undefined precision. When both edge sets are empty, F1 is undefined. An incomplete cohort produces a null full-cohort metric for the affected method. The reported signed gap is 0.90 minus F1.

## Reproduce on Windows

Use the project virtual environment with Python 3.12, the dependencies in `requirements-v3.lock`, and Windows OCR. Set up the pinned local recognition weights with `scripts\setup_local_ocr.py`. The V3 launcher runs these setup steps. The reported runs used Windows.Media.Ocr in en-US on Windows build 10.0.26100.

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

To evaluate an approved remote full-image vision model, add `--method vision-api --provider openai --model MODEL_ID` or `--method vision-api --provider anthropic --model MODEL_ID` to a benchmark command and choose a new report path. These modes send the drawing images to the configured service.

To evaluate the optional ChatGPT label-crop workflow, sign in through the workbench and use `--method subscription-crops --model MODEL_ID` with a separate report path. This workflow traces lines locally and sends image crops around typed equipment with unread names to OpenAI. It uses the connected account's plan allowance. The public benchmark PNGs contain the same pixels in both workflows.

V3 rereads eligible blank labels with PP-OCRv6 small recognition from PaddleOCR, using the Apache-2.0 ONNX conversion registered by RapidAI. The setup script verifies SHA-256 `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` before installing the weights in the project virtual environment. [Upstream model and license](https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx) · [RapidOCR conversion provenance and attribution](https://rapidai.github.io/RapidOCRDocs/latest/en/model_licenses/) · [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
