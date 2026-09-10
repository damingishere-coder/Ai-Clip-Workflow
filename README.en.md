<div align="center">

<img src="docs/images/niuma-banner.svg" alt="NiuMa Studio — local AI highlight workspace" width="100%" />

# NiuMa Studio

**From long videos to moments worth sharing.**

Transcribe, find highlights with AI, review clips, prepare content and schedule on your Windows PC.

[简体中文](README.md) · [Quick start](#quick-start) · [Documentation](docs/README.md) · [Changelog](CHANGELOG.md)

[![CI](https://github.com/damingishere-coder/Ai-Clip-Workflow/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/damingishere-coder/Ai-Clip-Workflow/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-2.3.0-2563eb)](CHANGELOG.md)
![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078d4)
![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab)
[![MIT](https://img.shields.io/badge/License-MIT-64748b)](LICENSE)

</div>

## One workspace for the whole process

| Find the content | Produce the clip | Learn from results |
| --- | --- | --- |
| Upload, local files and NAS sources | Review transcripts and adjust cut points | Import Douyin metrics and confirm attribution |
| Local faster-whisper transcription | FFmpeg clips and a separate subtitle workspace | Weekly reviews and Prompt version comparisons |
| AI candidates and structured scoring | Titles, topics, cover frames and scheduling | Confirm improvements and preview adaptive schedules |

```mermaid
flowchart LR
    A[Import] --> B[Transcribe and select]
    B --> C[Human review]
    C --> D[Create clips]
    D --> E[Prepare and schedule]
    E --> F[Publishing records]
    F --> G[Content review]
    G -. Confirm improvements .-> B
```

**Local-first, with configurable processing.** Media, SQLite data and browser sessions stay on your PC. Remote transcription or AI services receive the audio or text required for that operation. AI supports a controlled Codex CLI process, with OpenAI-compatible / DeepSeek and Ollama compatibility options. Each requires its own runtime or account configuration; a local CLI does not imply local model inference.

## Preview

These existing public screenshots are sanitized and illustrate the workflow. Individual controls may differ in the current version.

![NiuMa Studio dashboard](docs/images/dashboard.webp)

<details>
<summary><strong>Task details, clip review and publishing center</strong></summary>

![Task details](docs/images/task-detail.png)

![Clip review](docs/images/clip-review.webp)

![Publishing center](docs/images/publish-center.png)

</details>

## Quick start

### Native Windows setup

Install **Windows 10 / 11, Python 3.12+, Git and FFmpeg / FFprobe**, available from PowerShell. Real publishing also requires Google Chrome.

```powershell
git clone https://github.com/damingishere-coder/Ai-Clip-Workflow.git
cd Ai-Clip-Workflow
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\setup.ps1
.\scripts\start_native.ps1
```

Open **[the local workspace](http://127.0.0.1:8001)**, configure transcription and AI, then use a short test video to complete import, analysis, review and local clip generation.

- Setup preserves an existing `.env` and initializes local tokens and directories on first use.
- Native startup defaults to drive E. To use another location, run `.\scripts\start_native.ps1 -StorageRoot D:\NiuMaData` with your own path.
- Offline transcription requires downloaded models. See [deployment](docs/DEPLOYMENT.md) for NVIDIA GPU setup.
- If RunDock manages the app, use its existing service rather than duplicating ports `8001` / `8765`.
- Stop a manually started instance with `.\scripts\stop_native.ps1`.

Detailed guides are currently in Chinese: [getting started](docs/PROJECT_GUIDE.md), [startup modes](docs/PORTABLE_SETUP.md), [deployment](docs/DEPLOYMENT.md).

### Isolated demo

With Docker Desktop installed and running, execute from the cloned repository:

```powershell
.\scripts\start.ps1 -Demo
```

The demo uses fictional tasks, a separate database and `manual_export` drafts. Scheduling is disabled; no API key or real platform account is required. Stop with `.\scripts\stop.ps1 -Demo`. Reset samples with `.\scripts\start.ps1 -Demo -ResetDemo`.

## Capabilities and boundaries

| Capability | Status |
| --- | --- |
| Import, transcription, AI selection, review and clipping | Implemented; selected models and services require configuration |
| AI recovery | Per-unit progress and evidence; uncertain calls require confirmation before retry |
| Content reviews, weekly summaries and adaptive scheduling | Implemented, with confirmation and preview steps |
| Douyin publishing | Windows Chrome Worker; login and per-account validation required |
| Bilibili | Backend and historical compatibility retained; current UI and automatic synchronization disabled |
| Subtitles | Separate workspace; automatic workflows do not burn subtitles in |
| Multi-user cloud hosting | Not supported; designed for local single-user operation |

> [!IMPORTANT]
> Real submissions require rights to the source media, platform login, verification and content review. The project does not bypass CAPTCHA or platform controls. Uncertain publishing results require human review. Start by generating a local clip before testing publishing.

## Versions and updates

**2.3.0 consolidates the existing local runtime into `master`.** `VERSION` identifies the source version; [GitHub Releases](https://github.com/damingishere-coder/Ai-Clip-Workflow/releases) identifies published releases. Updating the version number does not replace release acceptance.

Use short-lived `codex/*` branches targeting `master`, passing checks before merging and updating the existing service. Unmerged experiments are not stable releases. See [branch maintenance](docs/BRANCHING.md).

Before updating, identify the actual runtime directory and create a backup:

```powershell
.\scripts\pre_upgrade.ps1
git status --short --branch
```

Only run `git pull --ff-only` on `master` with a clean working tree and no local-only commits. Restart the existing service using the [deployment guide](docs/DEPLOYMENT.md). Preserve local changes before switching.

## Documentation and contribution

[Current project status (Chinese)](PROJECT_STATUS.md) · [Next steps (Chinese)](NEXT_STEPS.md)


| Goal | Guide |
| --- | --- |
| Install and process a first video | [Getting started](docs/PROJECT_GUIDE.md) |
| Explore startup modes and demo | [Portable setup](docs/PORTABLE_SETUP.md) |
| Protect data and roll back | [Backup and restore](docs/BACKUP_AND_RESTORE.md) |
| Understand the implementation | [Technical reference](docs/TECHNICAL_REFERENCE.md) · [Architecture](docs/ARCHITECTURE.md) |
| Follow versions and plans | [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) |
| Maintain branches and releases | [Branch maintenance](docs/BRANCHING.md) · [Release checklist](docs/RELEASE_CHECKLIST.md) |
| Report an issue or contribute | [Issues](https://github.com/damingishere-coder/Ai-Clip-Workflow/issues) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) |

Reproducible bug reports, installation feedback, documentation improvements and tests are welcome. Do not upload private media, databases, API keys, cookies or personal information from logs.

Licensed under [MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md).
