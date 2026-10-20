# Third-party notices

The project's original code and documentation are covered by the
[MIT license](LICENSE). Third-party components retain their own licenses and
terms. This notice records the boundaries relevant to the checked-in artifacts;
it is not an exhaustive dependency-license audit.

## Bundled export font

`backend/app/assets/fonts/ExportSans.ttf` is a subset of **Noto Sans Regular**,
copyright 2022 The Noto Project Authors. Its embedded family/copyright metadata
identifies the [Noto Latin/Greek/Cyrillic project](https://github.com/notofonts/latin-greek-cyrillic).
The font remains under the [SIL Open Font License 1.1](backend/app/assets/fonts/OFL.txt),
which is included alongside it. The project MIT license does not replace the
font license.

## Bundled workspace font

The web workspace bundles the variable **Geist** font from the
[Google Fonts distribution](https://github.com/google/fonts/tree/main/ofl/geist).
Its [SIL Open Font License 1.1](frontend/public/fonts/OFL.txt) is retained beside
the font. It is served locally; opening the workspace makes no font request to
Google or another font service.

## Models and datasets

The recorded workflow integrates [UniMERNet-small](https://huggingface.co/wanderkid/unimernet_small)
and [Microsoft TrOCR](https://huggingface.co/microsoft/trocr-base-handwritten).
Their pretrained weights are downloaded separately and are not distributed in
this repository. Consult each upstream model card/license before downloading,
deploying, or redistributing it. Download manifests record the resolved model
revision and digest.

Training/evaluation tools expect independently supplied, appropriately licensed
data. IAM, MathWriting, CROHME, and other external datasets are not licensed by
the project's MIT grant. See the [data card](docs/reference/data-card.md) and
[artifact/data boundary](docs/reference/model-artifacts.md).

## Demo material

The [demo](demos/README.md) consists of application screen recordings and
screenshots. The formula input is a generated printed smoke fixture. The prose
input is recorded as a public IAM-derived OCR fixture in the dated evidence;
the exact original image URL and redistribution terms were not recorded there.
The project license does not grant rights to that underlying handwriting sample.
Confirm its provenance/terms or replace it with author-owned handwriting before
reusing the demo media outside this repository. No dataset corpus or private
user-note collection is included.

## Runtime dependencies

Dependencies are installed from their package distributions and retain their
own notices. The direct dependency manifests are
[`backend/requirements.txt`](backend/requirements.txt),
[`frontend/package.json`](frontend/package.json), and
[`ios/Package.swift`](ios/Package.swift). Optional runtime/training manifests
remain separate. Retain applicable upstream notices when distributing a built
application or container.

The optional production profile runs [SeaweedFS](https://github.com/seaweedfs/seaweedfs),
licensed under Apache-2.0, as a separate container. Its image and bundled
dependencies retain their upstream notices. The retired MinIO images are no
longer used by this profile.
