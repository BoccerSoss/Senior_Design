AIP SYNTHETIC FOUR-COMPONENT DRAWING PACKAGE

Purpose and status
This computer-generated educational fixture is a substitute for Greg to accept
or redraw by hand. It is not handmade. Greg's acceptance is pending. The reference
status is "pending independent human review". Automated and AI inspection do not
satisfy the required second-student check. No compliance with ISA, DOE, DEXPI or
any plant standard is claimed. This is not an operating or isolation drawing.

Extraction boundary
Run interpretation in a NEW session given ONLY input/four_component.png and
prompts/extract_from_image.txt. Do not upload this whole package or zip, this
README, truth/, source/, the manifest, or the generation chat. This chat already
knows the answer and cannot be called a blind extraction test. No extraction or
scientific accuracy evaluation has been performed by package generation.

Shapes and source authority
Shape conventions follow the public DOE handbook figures listed below.
The relevant DOE figures were visually inspected:
- DOE-HDBK-1016/1-93, Module 2 PR-02, page 3, Figure 1 (PDF page 55): opposed
  triangles adapted as a generic valve, without assigning a valve subtype/state.
- PR-02, page 14, Figure 15 (PDF page 66): roofed tank and circular pump with
  center entry and top tangent outlet. The arrow-free pump alternative is used.
The local legend governs this simplified fixture: no valve position, pump state
or flow direction is asserted. Symbol scaling/placement and pipe port locations
are educational adaptations. DEXPI is retained as contextual source material;
this package is not DEXPI XML or a DEXPI-conformance implementation. Exact URLs,
source hashes and locators are in source/symbol_reference.json.

Counting and graph convention
Count only tagged equipment in the process drawing. Pipes, labels, legend text,
the tank roof, and integral pump inlet/outlet strokes are not extra equipment.
Edges mean immediate undirected adjacency. A path through another component
does not create a direct edge. No direction is asserted by the matrix.

Expected answer (evaluator/reviewer only)
Components: T-101 tank; V-101 valve; P-101 pump; T-102 tank.
Direct edges: T-101--V-101; V-101--P-101; P-101--T-102.
Matrix order (both axes): T-101, V-101, P-101, T-102.
0,1,0,0
1,0,1,0
0,1,0,1
0,0,1,0

Files and coordinates
input/four_component.png is the primary extractor image, 1800 x 800 RGB pixels.
source/four_component.svg is the editable vector, with embedded licensed fonts.
source/generate_diagram.py uses one primitive scene for SVG and Pillow raster
drawing. It is a generator, not an image extractor. Raster antialiasing and SVG
stroke/font rendering can differ slightly between renderers; the PNG is the
canonical image for reference boxes. Source text remains editable in the SVG.
truth/components.json and truth/reference.json hold original-image pixel boxes.
Origin: top-left; x right, y down; [xmin,ymin,xmax,ymax], minima inclusive and
maxima exclusive. Boxes enclose only symbol ink, including antialiasing and
integral ports, excluding tags and external piping. They are generated reference
annotations, never extracted predictions. The simple version 1.0.0 JSON can be
mapped into Sol's future schema; no existing software schema was found.
truth/edges.csv uses source,target (orientation has no meaning).
truth/adjacency.csv includes row and column labels in the explicit order above.
truth/validation.json records automated checks, not human approval.
manifest.json hashes every packaged file except itself and its digest sidecar.
manifest.sha256 hashes the manifest. The zip is an outer transport wrapper and is not self-hashed by
the manifest; a separate sibling .zip.sha256 file accompanies it.

Optional degradation test
variants/four_component_lowres_blur.png is a separate 900 x 400 version made by
downsampling the clean raster by 0.5 in each axis and applying Gaussian blur
sigma 0.4 output pixels. Topology is unchanged. Use it in a separate new session
only after the clean test. Do not apply the original boxes directly to it; use
the 0.5 scale transform (round minima down and maxima up for enclosing boxes).

Reproduction (PowerShell, from the package root)
Python 3.12 and Pillow 13.0.0 were used; actual tool versions are in manifest.
Fonts and license are packaged in source/fonts/; no font download is required.
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r source/requirements.txt
  .\.venv\Scripts\python.exe source/generate_diagram.py --output ../aip_reproduced
  .\.venv\Scripts\python.exe ../aip_reproduced/source/generate_diagram.py --verify-only
  .\.venv\Scripts\python.exe ../aip_reproduced/source/generate_diagram.py --zip-only
Generate into a fresh folder to preserve annotations or reviewer edits. Exact
PNG bytes depend on the pinned Pillow/FreeType/zlib stack; those versions are
recorded. Geometry and reference topology are deterministic. No current time,
randomness or external network lookup is used by the generator. It writes plain
RGB PNGs with only IHDR/IDAT/IEND chunks, with no answer-bearing metadata.

Checks and limitations
The generator verifies unique IDs, four equipment records, three unique direct
edges, valid endpoints, no self-loops, symmetric zero-diagonal adjacency, labeled
CSV agreement, in-bounds boxes, uninterrupted raster pipe contacts, PNG chunks,
the allowlisted primary input folder, and SHA-256 integrity of all manifest files.
Inspect the finished PNG for readable labels, symbol count, contacts and stray
connections. Automated checks use known generator geometry; they do not prove
extraction performance or replace human reference review. This tiny clean fixture
does not test branches, crossings, instruments, degraded real drawings or general
P&ID capability. It cannot establish an overall accuracy/F1 target.

BLANK HUMAN REVIEW RECORD
Greg acceptance or redraw decision: ________________________________________
Greg name/signature: __________________________ Date/time/zone: ____________
Replacement handmade drawing ID/path (if any): _____________________________
Second-student verifier name: _____________________________________________
Verification date/time/time zone: __________________________________________
Drawing filename and SHA-256 checked: _____________________________________
Component tags/types and count checked: ___________________________________
Direct edges and labeled matrix checked: __________________________________
Original-pixel bounding boxes checked: ____________________________________
Symbol conventions and source figures checked: ____________________________
Discrepancies/corrections: ________________________________________________
Disposition/signature: ____________________________________________________
Official scoring authorization/status: ____________________________________

SOL HANDOFF
Primary image: input/four_component.png, 1800 x 800.
Reference: truth/reference.json, schema version 1.0.0.
Graph: immediate undirected equipment adjacency; no flow or state inference.
Independent human review and Greg acceptance are pending. Import the reference
only into evaluation code. Keep it inaccessible to the inference stage.
