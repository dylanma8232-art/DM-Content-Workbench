<div align="center">

# Content Workbench (`DM-Content-Workbench`)

**Turn scattered interviews and source material into evidence-backed topics and reviewed drafts, while tracking multi-platform account analytics in one local workspace**

<p>
  <a href="README.md"><img alt="简体中文" src="https://img.shields.io/badge/语言-简体中文-2b2927?style=flat-square"></a>
  <a href="README_EN.md"><img alt="English" src="https://img.shields.io/badge/Language-English-c8521b?style=flat-square"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776ab?style=flat-square">
  <img alt="SQLite Local Storage" src="https://img.shields.io/badge/Storage-Local_SQLite-38685c?style=flat-square">
  <a href="LICENSE"><img alt="License: CC BY-NC-SA 4.0" src="https://img.shields.io/badge/License-CC_BY--NC--SA_4.0-b45309?style=flat-square"></a>
</p>

[Highlights](#highlights) · [Quick Start](#quick-start) · [Core Capabilities](#core-capabilities) · [License](#license)

</div>

---

## Highlights

<p align="center">
  <img src="assets/overview.png" alt="Account Overview — multi-platform metrics and 7-day trends" width="100%">
</p>

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="assets/evidence.png" alt="Cross-source topic candidates with verbatim quotes" width="100%">
      <br><sub><b>Evidence-backed topics</b>: verify every suggested angle against verbatim source quotes</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="assets/draft-review.png" alt="Versioned draft review and export" width="100%">
      <br><sub><b>Versioned review & export</b>: review decisions bind to exact draft versions with full traceability</sub>
    </td>
  </tr>
</table>

---

## Quick Start

Requires **Python 3.12** (UI is in Simplified Chinese; works locally with SQLite and no external accounts required):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in your browser. On Windows, use `py -3.12 -m venv .venv` and `.venv\Scripts\python.exe`. For Docker self-hosting, run `docker compose up --build -d --wait`.

## Core Capabilities

- **Multi-platform analytics overview**: track followers, views, engagements, posts, and 7-day trends via manual entry, CSV import, or mapped HTTPS JSON feeds.
- **Multi-source collection**: import CSV/TXT, PDF (optional Poppler + Tesseract OCR), audio/video (FFmpeg + OpenAI-compatible ASR), HTTPS RSS/Atom, static web pages, JSON APIs, and Notion databases.
- **Evidence-to-draft workflow**: clean and deduplicate batches, surface cross-source topic clues with verbatim citations, draft locally or with an OpenAI-compatible LLM, bind review decisions to specific draft versions, export Markdown, or push approved drafts to webhooks.
- **Local-first storage & backup**: SQLite keeps all projects, materials, and drafts on disk, with built-in export/restore (`backup.py`) and optional Notion JSON snapshots. See [.dm.env.example](.dm.env.example) and the [full Chinese guide](README.md).

---

## License

Licensed under **[CC BY-NC-SA 4.0](LICENSE)** (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International) for personal and non-commercial use. Commercial use is prohibited; adaptations must preserve attribution and be shared under the same license. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Author

Built by **[dylanma8232-art](https://github.com/dylanma8232-art)** · **[Visit profile for more open-source projects →](https://github.com/dylanma8232-art)**
