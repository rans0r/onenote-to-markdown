# Getting the raw `.one` files out of OneDrive

OneDrive stores a OneNote notebook as a **folder** containing one `.one` file
per section, one sub-folder per section group, and an `Open Notebook.onetoc2`
table of contents. The OneDrive web UI hides this: a notebook shows up as a
single item, its context menu has no *Download* entry, opening it launches
OneNote Online, and navigating to the folder's URL directly gives "Sorry,
something went wrong". Even the search results list the individual sections but
won't let you download them.

The trick is that **ordinary folders can be downloaded as a zip, and the zip
includes any notebooks inside them**. So either download a folder that already
contains the notebook, or copy the notebook into a fresh folder and download
that.

## Option A — copy the notebook into a folder, download the folder

1. Sign in at <https://onedrive.live.com> and open **My files**. Notebooks
   appear among the *files* (not the folders) of whatever folder holds them,
   with the purple OneNote icon. If you don't know where one lives, use the
   search box: filter **More → OneNote** and read the *Location* column, or
   look in *Home → Recent*.
2. Right-click the notebook → **Copy to**.
3. In the *Copy to* dialog press **New folder**, give it a name such as
   `Notebook-export`, press **Create**, select the new folder and press
   **Copy here**. (Copy rather than move — moving a notebook can break the
   sync links other OneNote clients hold. The dialog's folder list scrolls
   poorly with a mouse wheel; dragging its scrollbar works.)
4. Open the new folder; the notebook copy is inside it.
5. With nothing selected, press **Download** in the toolbar. OneDrive zips the
   folder and the browser saves `Notebook-export.zip`.
6. Unzip it. The section files are under `Notebook-export/<Notebook>/`.
7. Delete the export folder from OneDrive when you are done — it was only
   needed for the download.

## Option B — download the whole parent folder

If several notebooks live in one folder (say `Documents`), open that folder
and press **Download** with nothing selected. You get everything in the folder
as one zip; pull out just the notebook files with

```bash
unzip -q Documents.zip '*.one' '*.onetoc2' -d notebooks
python -m onenote_to_markdown notebooks/Documents/* -o notes --versions
```

This is the fastest route for a large collection; a 250 MB folder with forty
notebooks reduced to ~60 MB of `.one` files.

## Option C — Microsoft Graph (scriptable)

`GET https://graph.microsoft.com/v1.0/me/drive/root:/<Notebook>:/children`
lists the section files; each item's `@microsoft.graph.downloadUrl` fetches the
raw bytes. You need an OAuth token — [Graph Explorer]
(https://developer.microsoft.com/graph/graph-explorer) is the quickest way to
get one interactively. Good for automating many notebooks or recurring exports.

## Option D — OneDrive sync client

If the notebook's folder is synced to your computer, the `.one` files are on
disk under your OneDrive folder in the same packaging format. (On macOS look
under `~/Library/CloudStorage/OneDrive-Personal/`.)

## What you get

* One `.one` per section. Section groups are sub-folders with their own
  `.onetoc2`. Deleted sections and pages sit in `OneNote_RecycleBin/`
  (including `OneNote_DeletedPages.one`) and are exported too — they're often
  the reason to do this in the first place.
* The files are in OneNote's **alternative packaging** (MS‑FSSHTTPB cell
  storage; header GUID `{638DE92F-A6D4-4BC1-9A36-B3FC2511A5B7}`), not the
  classic revision-store format desktop OneNote writes locally. Most other
  open-source `.one` readers only understand the classic format, which is why
  this project exists.
* `Open Notebook.onetoc2` is a classic-format stub whose real payload (section
  list, order, colors) is an alternative-packaging store embedded right after
  the 1,216-byte header. `onenote-to-markdown` reads it to reproduce OneNote's
  section order.

## What does not work

* Right-click → Download on the notebook item (no such menu entry).
* Navigating to the notebook folder's URL (`…/my?id=…/Notebook`) — it errors.
* OneNote Online has no export; OneNote for Mac cannot export `.one` or
  `.onepkg`; only OneNote for Windows can, and it writes the classic format.
* Calling `my.microsoftpersonalcontent.com/_api/...` from the browser console
  — it needs the web app's bearer token and CORS blocks it.
