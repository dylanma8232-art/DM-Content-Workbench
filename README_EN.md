# Content Workbench (DM-Content-Workbench)

[简体中文](README.md) · [License](LICENSE)

A local workspace for creators and editors. Compare platform reports, collect source material, check quotations before choosing a topic, and track reviews by draft version.

![Checking a suggested topic against its sources](assets/evidence.png)

## Start locally

1. Install Python 3.12 and download or clone the repository.
2. Create a virtual environment and install the dependencies.

       python3 -m venv .venv
       .venv/bin/python -m pip install -r requirements.txt

3. Launch the server and open http://127.0.0.1:8765 in your browser.

       .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765

On Windows, create the environment with `py -3.12 -m venv .venv` and run `.venv\Scripts\python.exe`. The UI is in Simplified Chinese. Start with an empty workspace or load synthetic material from the import page. The local evidence-to-draft workflow works without external accounts.

## Data and integrations

SQLite keeps projects, reports, materials and drafts on the host. Configure a destination under Settings to migrate the workspace. Connect RSS, static websites, JSON APIs or Notion for material collection. Platform reports support manual entry, CSV and mapped HTTPS JSON. Approved drafts can be sent manually to a configured webhook. Optional credentials belong in the server environment. See [.dm.env.example](.dm.env.example) and the [operator guide](README.md).

## License

Licensed under **[CC BY-NC-SA 4.0](LICENSE)** (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International) for personal and non-commercial use. Commercial use is prohibited; adaptations must preserve attribution and be shared under the same license. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Author: [dylanma8232-art](https://github.com/dylanma8232-art).
