# Local model and iPad setup

Install the [application dependencies](getting-started.md) first. A clean
checkout contains no weights or optional model runtime. The pinned macOS arm64
setup below was exercised in the [October 11 acceptance](../release-evidence/END_TO_END_LOCAL_2026-10-11.md);
installed services, simulator IDs and local test counts are historical evidence.
Use [status](../STATUS.md) for current capability limits.

## Formula artifact and runtime

| Item | Verified configuration |
| --- | --- |
| Upstream snapshot | [wanderkid/unimernet_small](https://huggingface.co/wanderkid/unimernet_small) |
| Resolved revision | `82cb85440138c2038680fca410c8f70bb7343d6d` |
| Snapshot tree SHA-256 | `3c4b36213bb7c7f773e04ef0f5022fd1b7934d5a489869116010a63961476859` |
| Model directory | `backend/var/objectstore/models/unimernet_small` |
| Optional Python environment | `backend/var/objectstore/models/unimernet_runtime/.venv` |
| Runtime | Python 3.12, UniMERNet 0.2.3, Torch 2.5.1, Transformers 4.42.4 |
| Execution | CPU, four Torch threads, offline inference |
| Image processor | Upstream FormulaImageEvalProcessor, 192 × 672; grayscale tensor repeated into three channels |

Weights and the optional environment are ignored by Git. The artifact manifest
records the download source and revision; its tree digest was rechecked after
inference. The existing API environment keeps its own dependencies. The official
`.pth` checkpoint needs upstream UniMERNet code, so the adapter now starts a
separate cached worker, strictly loads the checkpoint, probes real inference,
and forwards token/beam limits and truncation. Failed probes and timeouts return
unavailable results.

First install the Python 3.12 API environment using
[local development setup](getting-started.md#local-development). To reproduce
this setup on macOS arm64, run from the repository root:

```sh
backend/.venv/bin/python scripts/download_unimernet.py \
  --destination backend/var/objectstore/models/unimernet_small \
  --revision 82cb85440138c2038680fca410c8f70bb7343d6d
backend/.venv/bin/python -m venv \
  backend/var/objectstore/models/unimernet_runtime/.venv
backend/var/objectstore/models/unimernet_runtime/.venv/bin/python -m pip install \
  -r backend/requirements-unimernet-macos.txt
backend/var/objectstore/models/unimernet_runtime/.venv/bin/python -m pip check
```

Start the two services in separate terminals:

```sh
HND_FORMULA_ENGINE=unimernet make dev-backend
make dev-frontend
```

Verify <http://127.0.0.1:5173/api/health>: `formula_model.loaded` should be
true, with backend `unimernet-small`. After the TrOCR installation below,
`prose_model.loaded` should be true with backend `trocr-handwritten`. The
legacy CRNN `model_status` stays unavailable without its separate artifact.
First-load probes can take several seconds; subsequent inference reuses the
worker. See the dated acceptance report for recorded smoke results and timings.

This optional environment was tested on macOS arm64. The Linux Docker image
does not install it, and a Mac virtual environment cannot be reused in a Linux
container. Docker model setup needs a compatible Linux environment separately.

## Handwritten prose artifact

The [Microsoft TrOCR model](https://huggingface.co/microsoft/trocr-base-handwritten)
uses revision `eaacaf452b06415df8f10bb6fad3a4c11e609406`, tree SHA-256
`66074482f479dc42847eb1fd948ac8e75b9fcd8ed743a31cfec292c5c6db6a6e` and the
upstream MIT license. It shares the isolated Torch/Transformers environment
above and runs offline from `backend/var/objectstore/models/trocr_handwritten`.

```sh
backend/.venv/bin/python scripts/download_trocr.py \
  --destination backend/var/objectstore/models/trocr_handwritten \
  --revision eaacaf452b06415df8f10bb6fad3a4c11e609406
```

It is a line recognizer. Select **Handwritten prose** on the website, then
**Convert page** or **Convert crop**; the native editor uses **Convert Prose**.
Human review is required. The dated fixture's incorrect prediction is documented
in [status](../STATUS.md), and does not establish representative quality.

## Simulator setup

The simulator uses local ad-hoc signing, which needs no Apple developer account.
Disabling signing caused a Keychain error on normal launch; keep local signing
enabled when running the app. The generated project is at
`ios/PersonalizedNotes.xcodeproj`.

```sh
cd ios
xcodegen generate
open PersonalizedNotes.xcodeproj
```

Choose the `PersonalizedNotes` scheme, an iPad simulator, and Run. Simulator
tests establish automated UI behavior; Pencil input and actual hardware behavior need an iPad.

## Your physical iPad

Physical installation needs an unlocked device and an Apple development
signing identity. Follow these steps:

1. Connect and unlock the iPad, accept **Trust This Computer** if shown, and
   enable Developer Mode if Xcode requests it.
2. Sign into your Apple developer account in Xcode Settings → Accounts.
3. In Signing & Capabilities, select your signing team for **PersonalizedNotes**
   and **ShareExtension**. Both use the App Group
   `group.com.handwrittennotes.digitizer`; the selected team must support and
   provision that group. Resolve any bundle identifier or provisioning messages
   shown by Xcode before installing. Keep both targets' shared group consistent.
4. Select the connected iPad and Run the `PersonalizedNotes` scheme.
5. Put the iPad and Mac on the same network. Restart the backend to listen on
   the Mac's network address, using the command below. In the iPad app's
   Settings → Backend, enter `http://<Mac-LAN-IP>:8000` and test the connection.

From the repository root, after stopping the existing backend:

```sh
cd backend
HND_FORMULA_ENGINE=unimernet .venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000
```

The physical iPad cannot reach the Mac through `127.0.0.1`. Allow local network
access when prompted. This HTTP address is for a Debug build on your local
network; a Release build needs HTTPS for non-loopback endpoints.

## Remaining acceptance inputs

Optional CRNN personalization requires the project's trained files:
`backend/var/objectstore/models/base_model/base_model.pt` and
`base_model.meta.json`. A generic pretrained OCR download does not match this
architecture. Provide a verified compatible checkpoint, or obtain the real IAM
dataset and use [the real-data training instructions](training.md).
The formula model is a separate task model and cannot substitute for this base.

For meaningful quality testing, prepare representative handwritten notes and
verified transcriptions. Keep final test documents separate from training data.

The native page-conversion worker, shared rendering and native review/export
are implemented. Focused tests cover immutable sources, changed ink, duplicate
requests, task availability, account isolation and restart recovery. Real-model
API journeys pass for both tasks. Full two-device conflict/interruption testing,
representative handwriting quality measurements and physical Pencil testing
remain separate acceptance gates; simulator setup does not establish them.

See [the setup evidence](../release-evidence/MODEL_AND_IPAD_SETUP_2026-10-11.md)
for the commands, results, and boundaries of this validation.
The recorded results are in
[the final local acceptance report](../release-evidence/END_TO_END_LOCAL_2026-10-11.md)
and [the conversion API evidence](../release-evidence/LIVE_CONVERSION_2026-10-11.json).
