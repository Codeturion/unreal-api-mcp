[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/codeturion-unreal-api-mcp-badge.png)](https://mseep.ai/app/codeturion-unreal-api-mcp)

# unreal-api-mcp

<!-- mcp-name: io.github.Codeturion/unreal-api-mcp -->

[![PyPI Version](https://img.shields.io/pypi/v/unreal-api-mcp.svg)](https://pypi.org/project/unreal-api-mcp/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/unreal-api-mcp.svg)](https://pypi.org/project/unreal-api-mcp/)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-green)](https://registry.modelcontextprotocol.io/?q=unreal-api-mcp)
[![GitHub Stars](https://img.shields.io/github/stars/Codeturion/unreal-api-mcp)](https://github.com/Codeturion/unreal-api-mcp)
[![GitHub Last Commit](https://img.shields.io/github/last-commit/Codeturion/unreal-api-mcp)](https://github.com/Codeturion/unreal-api-mcp)
[![Detect New UE Release](https://github.com/Codeturion/unreal-api-mcp/actions/workflows/detect-ue-release.yml/badge.svg)](https://github.com/Codeturion/unreal-api-mcp/actions/workflows/detect-ue-release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**MCP server that gives AI agents accurate Unreal Engine C++ API documentation. Saves tokens, context, and time. Prevents hallucinated signatures, wrong `#include` paths, and deprecated API usage.**

Works with Claude Code, Cursor, Windsurf, or any MCP-compatible AI tool. No Unreal Engine installation required. Check the [supported versions](https://github.com/Codeturion/unreal-api-mcp/releases/tag/db-v1). New versions are detected and built automatically every week.

## Quick Start

Add to your MCP config (`.mcp.json`, `mcp.json`, or your tool's MCP settings), setting `UNREAL_VERSION` to match your project:

```json
{
  "mcpServers": {
    "unreal-api": {
      "command": "uvx",
      "args": ["unreal-api-mcp"],
      "env": {
        "UNREAL_VERSION": "5.5"
      }
    }
  }
}
```

Set this to match your project's UE version. See [supported versions](https://github.com/Codeturion/unreal-api-mcp/releases/tag/db-v1) for all available databases.

On first run the server downloads the correct database to `~/.unreal-api-mcp/`. Patch versions (e.g. `"5.7.3"`) fall back to the major.minor database (e.g. `"5.7"`) if a patch-specific one isn't available.

## How It Works

1. **Version detection.** The server figures out which UE version to serve:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `UNREAL_VERSION` env var | `"5.5"`, `"5.7"`, `"5.7.3"` |
| 2 | `UNREAL_PROJECT_PATH` | Reads `.uproject` `EngineAssociation` field (e.g. `5.5.1` or `5.7`) |

Set one of these to match your project. Without either, the server defaults to UE 5.7.

2. **Database download.** If the database for that version isn't cached locally, it downloads from GitHub (one time). For patch versions, falls back to the major.minor database if needed. Also checks for updates on startup.

3. **Serve.** All tool calls query the version-specific SQLite database. Exact lookups return in <1ms, searches in <5ms.

Each version has its own database with the correct signatures, deprecation warnings, and member lists for that release.

## Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `search_unreal_api` | Find APIs by keyword | "character movement", "spawn actor", "K2Node" |
| `get_function_signature` | Exact signature with parameters and return type | `AActor::GetActorLocation` |
| `get_include_path` | Resolve `#include` for a type | "ACharacter" -> `#include "GameFramework/Character.h"` |
| `get_class_reference` | Full class reference card | "APlayerController", "UK2Node_SpawnActorFromClass", "UEdGraphSchema_K2" |
| `get_deprecation_warnings` | Check if an API is obsolete | "K2_AttachRootComponentTo" -> Use AttachToComponent() instead |

## Coverage

All Engine Runtime, Editor, Developer modules, plus built-in plugins (Enhanced Input, Gameplay Abilities, Common UI, Niagara, Chaos, and hundreds more).

Includes Blueprint graph internals: **158 UK2Node subclasses**, `UEdGraphSchema_K2`, `BlueprintGraph`, `KismetCompiler`, and `GraphEditor` modules (1,120+ entries). If you're writing custom K2 nodes or editor tooling, it's indexed.

See the full list of supported versions and databases on the [**db-v1 release page**](https://github.com/Codeturion/unreal-api-mcp/releases/tag/db-v1). New versions are detected and built automatically every Monday via CI.

Record breakdown (UE 5.7):

| Type | Count | Source |
|------|-------|--------|
| Classes (UCLASS) | 10,075 | `AActor`, `ACharacter`, `UGameplayStatics`, ... |
| Structs (USTRUCT) | 9,014 | `FHitResult`, `FVector`, `FTransform`, ... |
| Enums (UENUM) | 3,475 | `EMovementMode`, `ECollisionChannel`, ... |
| Functions (UFUNCTION) | 23,414 | Signatures with params, return types, specifiers |
| Properties (UPROPERTY) | 66,340 | Types, specifiers, doc comments |
| Delegates | 2,406 | Dynamic multicast, delegate declarations |

Does **not** cover third-party plugins or marketplace assets. For those, rely on project source.

## Benchmarks

Measured, not promised: 14 research questions across 2 testbeds, answered by 3 agent configs, every answer judged against ground truth verified beforehand (API facts against the docs database, source facts against the UE 5.8 engine tree). The full harness lives in [`docs/benchmark/`](docs/benchmark/) and re-runs with one command.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Codeturion/unreal-api-mcp/master/docs/images/benchmark-accuracy-dark.png">
  <img alt="Answer quality by agent config, judged against verified ground truth" src="https://raw.githubusercontent.com/Codeturion/unreal-api-mcp/master/docs/images/benchmark-accuracy-light.png">
</picture>

| Config | Correct | Partial | Wrong |
|---|---|---|---|
| **MCP + targeted Read** | **14/14** | 0 | 0 |
| Skilled (Grep+Read) | 11/14 | 2 | 1 |
| Naive (full Reads) | 12/14 | 1 | 1 |

The gap is not where you would expect. On well-documented includes and signatures, and on grep-able implementation facts inside a 14,000 line engine file, all three configs tie. Modern models know the documented UE surface and search local source competently. The gap opens on recent deprecations. Asked whether `UKismetSystemLibrary::IsSplitScreen` is still valid in 5.8, both non-MCP agents got it wrong: one declined, one asserted it was fine. It is deprecated in favor of `HasMultipleLocalPlayers`. Both also mischaracterized the 5.5-era change that made direct `AActor::NetUpdateFrequency` access deprecated. The MCP agent answered all three deprecation questions correctly, because the deprecation flag and replacement hint are in the database.

Why correctness rather than raw output? Agentic tools search code well now. Claude Code has shipped a Grep tool from the start, so an agent can find anything that is actually in your files. The problem is the parts that are not in your files: exact signatures, `#include` paths, and especially fresh deprecations. Those are not reliably in a model's memory either, and getting one wrong costs you a broken build or a silently deprecated call.

<details>
<summary>Methodology</summary>

- 2 testbeds: 8 pure Unreal API lookups (exact signatures, `#include` paths, deprecations) and 6 questions answered inside `CharacterMovementComponent.cpp` (about 14,000 lines) from the UE 5.8 engine source
- 3 configs, same model (Sonnet) and turn limit: MCP tools + Grep/Read, Grep/Read only, Read only
- The API sweep runs with no engine source present, the realistic case when writing UE C++ in your own project, so non-MCP configs answer from model memory. The source sweep gives every config the same files to grep
- Ground truth verified before any runs; answers judged by a separate model session against that ground truth
- Run it yourself: `python docs/benchmark/run.py --cwd <dir> --questions <file>` (results from July 2026; agent behavior moves, so re-run before quoting). Point it at your own UE project to reproduce the project-research scenario

</details>

<details>
<summary>Query latency</summary>

Measured on UE 5.7 database (114,724 records), 50 iterations per query:

| Query | Median | p95 |
|-------|--------|-----|
| Exact FQN lookup (`get_function_signature`) | <1ms | <1ms |
| FTS search: specific function name | <1ms | <1ms |
| FTS search: keyword ("spawn actor") | 1ms | 1ms |
| Include path resolution | 2ms | 2ms |
| Class reference (full member list) | 22ms | 23ms |
| Deprecation check | 24ms | 25ms |

</details>

## CLAUDE.md Snippet

Add this to your project's `CLAUDE.md` (or equivalent instructions file). **This step is important.** Without it, the AI has the tools but won't know when to reach for them.

```markdown
## Unreal Engine API Lookup (unreal-api MCP)

Use the `unreal-api` MCP tools to verify UE C++ API usage instead of guessing. **Do not hallucinate signatures or #include paths.**

| When | Tool | Example |
|------|------|---------|
| Unsure about a function's parameters or return type | `get_function_signature` | `get_function_signature("AActor::GetActorLocation")` |
| Need the `#include` for a type | `get_include_path` | `get_include_path("ACharacter")` |
| Want to see all members on a class | `get_class_reference` | `get_class_reference("UCharacterMovementComponent")` |
| Searching for an API by keyword | `search_unreal_api` | `search_unreal_api("spawn actor")` |
| Checking if an API is deprecated | `get_deprecation_warnings` | `get_deprecation_warnings("K2_AttachRootComponentTo")` |
| Writing custom K2 nodes or editor tools | `get_class_reference` | `get_class_reference("UK2Node_SpawnActorFromClass")`, `get_class_reference("UEdGraphSchema_K2")` |

**Rules:**
- Before writing a UE API call you haven't used in this conversation, verify the signature with `get_function_signature`
- Before adding a `#include`, verify with `get_include_path` if unsure
- Covers: all Engine Runtime/Editor modules, built-in plugins (Enhanced Input, GAS, CommonUI, Niagara, etc.), Blueprint graph internals (UK2Node subclasses, EdGraphSchema, BlueprintGraph, KismetCompiler)
- Does NOT cover: third-party plugins or marketplace assets
```

## Setup Details

<details>
<summary>Auto-detect version from .uproject</summary>

Instead of setting `UNREAL_VERSION`, you can point to your Unreal project. The server reads the `EngineAssociation` field from your `.uproject` file:

```json
{
  "mcpServers": {
    "unreal-api": {
      "command": "uvx",
      "args": ["unreal-api-mcp"],
      "env": {
        "UNREAL_PROJECT_PATH": "F:/Unreal Projects/MyProject"
      }
    }
  }
}
```

</details>

<details>
<summary>Alternative installation methods</summary>

**Using pip install:**
```bash
pip install unreal-api-mcp
```
```json
{
  "mcpServers": {
    "unreal-api": {
      "command": "unreal-api-mcp",
      "args": [],
      "env": {
        "UNREAL_VERSION": "5.5"
      }
    }
  }
}
```

</details>

<details>
<summary>Environment variables</summary>

| Variable | Purpose | Example |
|----------|---------|---------|
| `UNREAL_VERSION` | UE version to serve | `5.5`, `5.7`, `5.7.3` |
| `UNREAL_PROJECT_PATH` | Auto-detect version from .uproject | `F:/Unreal Projects/MyProject` |
| `UNREAL_INSTALL_PATH` | Override UE install path (for `ingest` only) | `H:/UE_5.6` |

</details>

<details>
<summary>Building databases locally</summary>

If you want to build a database from your own Unreal Engine installation instead of downloading:

```bash
# Build for a specific version
python -m unreal_api_mcp.ingest --unreal-version 5.6 --unreal-install "H:/UE_5.6"
python -m unreal_api_mcp.ingest --unreal-version 5.5 --unreal-install "H:/UE_5.5"
```

Databases are written to `~/.unreal-api-mcp/unreal_docs_{version}.db` by default.

</details>

<details>
<summary>Project structure</summary>

```
unreal-api-mcp/
├── src/unreal_api_mcp/
│   ├── server.py          # MCP server (5 tools)
│   ├── db.py              # SQLite + FTS5 database layer
│   ├── version.py         # Version detection + DB download
│   ├── header_parser.py   # Parse Unreal C++ headers (UCLASS, UFUNCTION, etc.)
│   ├── unreal_paths.py    # Locate UE installs + discover modules
│   └── ingest.py          # CLI ingestion pipeline
└── pyproject.toml
```

Databases are stored in `~/.unreal-api-mcp/` (downloaded on first run).

</details>

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Could not download UE X database" | Check internet connection. Or build locally: `python -m unreal_api_mcp.ingest --unreal-version 5.6 --unreal-install H:/UE_5.6` |
| Wrong API version being served | Set `UNREAL_VERSION` explicitly. Check stderr: `unreal-api-mcp: serving UE <version>` |
| Server won't start | Check `python --version` (needs 3.10+). Check path: `which unreal-api-mcp` or `where unreal-api-mcp` |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | Version 0.3.0 or older installed mcp 2.x. Restart your MCP client, or run `uvx unreal-api-mcp@latest`. With pip, run `pip install -U unreal-api-mcp`. |
| Third-party plugins return no results | Marketplace/third-party plugins are not indexed. Only built-in Engine and Plugin APIs are covered. |

---

## See Also

**[unity-api-mcp](https://github.com/Codeturion/unity-api-mcp)**: Same concept for Unity (C#). Covers Unity 2022, 2023, and Unity 6.

## Contact

Need a custom MCP server for your engine or framework? I build MCP tools that cut token waste and prevent hallucinations for AI-assisted game development. If you want something similar for your team's stack, reach out.

fuatcankoseoglu@gmail.com

## License

[MIT](https://opensource.org/licenses/MIT)

Free to use, fork, modify, and share for any personal or non-commercial purpose.
Commercial use requires permission.
