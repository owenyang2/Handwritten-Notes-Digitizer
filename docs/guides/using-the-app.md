# Import, review and export

[Set up the app](getting-started.md) first. The screenshots below are generated
from the current production web build with a mocked API and synthetic lecture
notes. They show interface behavior, not recognition quality or a live backend.
The [capture manifest](../assets/screenshots/manifest.json) records source and
image hashes; see the [media workflow](../development/documentation.md#screenshots-and-videos).

## 1. Open Library and import notes

Choose **Import notes** and select a PDF, PNG or JPEG. Open a notebook from
Library to view its pages and retained source. The native app also supports
creating notebooks and drawing with PencilKit.

Search notebooks by title, choose **All notebooks / Archived / Trash**, or use
**Favorites** in navigation. **Grid view** shows source previews; **List view**
uses compact rows without changing your search or notebook links. The count
describes the loaded results; a `+` means more can be loaded. **New import** in
the sidebar opens the same import dialog while you are reviewing a notebook.

![Library in light appearance with a sample lecture notebook](../assets/screenshots/library-light-1440.png)

## 2. Review the result beside its source

Select a page and a region. When a compatible model is installed, request
prose or formula conversion. Compare the prediction with the highlighted source;
correct the prose or LaTeX and inspect the formula preview. Manual correction
remains useful when recognition is unavailable.

The **Page … of …** control opens the page navigator. Choose the recognition
task and **Convert page** beside it. On desktop, **View options** exposes the
source/result pane balance; source zoom and coordinate-based crop selection
remain below the original. Select **Next region** to continue reviewing without
returning to Library.

![Source-linked review in light appearance](../assets/screenshots/review-light-1440.png)

**Save** commits a reviewed revision. A save failure or version conflict keeps
the correction in the open page and offers retry/comparison. Unsaved web drafts
are lost on reload or closing the page. Saving a correction does not grant
training consent; that choice is explicit.

## 3. Export the saved review

Choose **Export**. If corrections are unsaved, choose **Save and export** or
**Export last saved version** deliberately. Web document exports include
Markdown, LaTeX, HTML, a LaTeX bundle and controlled PDF. Native original-source,
ink and converted exports have distinct purposes; compare the exported content
with the saved review.

## Appearance and small screens

Use **Theme → System / Light / Dark** in the top toolbar or on the sign-in
screen. **Settings → Appearance** offers the same choice. The preference is
stored locally; changing it does not alter original scans, ink or exports.
Light appearance uses warm paper and forest-green actions; dark appearance
uses warm charcoal and soft green. Outlined navigation and filled selection
buttons make the active notebook, layout and review pane easier to identify.
On small screens, navigation collapses and review places the selected source
above the correction. Use **Original / Result** to switch the main pane. Opening
navigation presents a full-screen drawer on phones. Returning to desktop width
restores the sidebar. Interface motion is short feedback on controls and
overlays, and follows the system reduced-motion preference.

| Dark desktop review | Compact light review |
| --- | --- |
| ![Source-linked review in dark appearance](../assets/screenshots/review-dark-1440.png) | ![Review at 320 pixels wide](../assets/screenshots/review-light-320.png) |

Native offline persistence and browser connectivity have different limits.
Check [current status](../STATUS.md) before relying on an unverified workflow.
