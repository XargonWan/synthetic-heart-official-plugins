# SyntH official extensions

The official extension repository of [Synthetic Heart](https://github.com/xargonwan/synthetic_heart)
(SyntH): a **catalog** of extensions, each published as its own `.zip` asset of a
**rolling GitHub release**. It is what the Plugins > Catalog of a SyntH installation reads,
and what a fresh installation installs its defaults from.

| Extension | What it does | Installed by default |
| --- | --- | --- |
| `official/synth_stage` | The SyntH Stage client (3D avatar, chat, voice) at `/stage/` | yes |
| `official/synth_dashboard` | The Control Deck (Dashboard): every setting as a knob, toggle or readout, in the More menu | yes |
| `official/openai_api` | An OpenAI-compatible API (`/v1/chat/completions`, SSE streaming) | yes |
| `official/telegram_bot` | Telegram bot interface | yes |
| `official/fluxer` | Fluxer (Discord-compatible) interface | yes |
| `official/zen` | Registers the Zen LLM Engine endpoint | yes |
| `official/telegram` | Telegram **user account** (login from the WebUI, voice and video calls) | no (162 MB) |
| `official/protocol_harmony` | Harmony AI Cloud protocol for external endpoints (model catalogue, TTS, STT) | yes |
| `official/protocol_fish` | Fish Audio protocol for external endpoints (cloud TTS and voices) | yes |
| `official/vox_kitten` | KittenTTS local voice (ONNX on the CPU, eight voices; the model is fetched on first use) | yes |
| `official/auris_vosk` | Vosk local speech recognition (offline, CPU; the model of your language is fetched on first use) | yes |
| `official/skin_miku` | Hatsune Miku skin (VRM by rurune; files only, shown in the Skins tab; see its `readme.txt` for the model's terms) | no |
| `official/skin_riko` | Riko skin (VRM by Little Forest; files only, shown in the Skins tab) | no |
| `official/discord` | Discord bot interface: servers, channels, threads, DMs, files, replies, reactions, voice channels and calls, classic or held by a Live engine such as `gemini_live` | no |
| `official/gemini_live` | Gemini Live engine: real-time voice conversations held by the model (needs a Gemini API key; untested against the real API) | no |
| `official/matrix` | Matrix interface (rooms, threads, replies, files) | no |
| `official/radio_host` | AzuraCast Radio DJ: the synth speaks between songs on your station (needs ffmpeg) | no |

## Releases

There is no release per update. Each branch has **one rolling release** whose tag moves to
the newest commit and whose assets are replaced:

| Branch | Release tag | |
| --- | --- | --- |
| `develop` | `latest-develop` | pre-release |
| `main` | `latest` | |

A release holds:

```
official-<name>.zip    one extension (a zip with its code and libraries; the version is in its manifest)
synth-repo.json        the catalog: every extension with its version, SHA-256, size, platform, capabilities
synth-repo.json.sig    the Ed25519 signature over the exact bytes of the catalog
```

The version of an extension is its own (in `synth-extension.toml` inside the zip and in the
catalog); the release does not carry one.

## Use it

SyntH adds this repository by itself the first time its extension runtime starts. By hand:

```bash
python -m core.extensions.cli repo add official github:XargonWan/synthetic-heart-official-plugins \
    --kind github --branch latest-develop --trusted \
    --key "$(curl -sL https://raw.githubusercontent.com/XargonWan/synthetic-heart-official-plugins/develop/public.key)"
python -m core.extensions.cli install official/telegram --grant
```

(`--branch` is the release tag: `latest` for the `main` channel.) Check the key against the
fingerprint before trusting it:

```
public key   cRgFJxeqnbfGN+CPSeXhINTQr7z7SxNCdYtzrNObXGY=
fingerprint  842730d171b5bef38249c0a1b76945e0
```

A package is verified against the signed catalog before it is unpacked, so GitHub does not
have to be trusted; and installing it **grants nothing**: the operator grants what the
manifest declares.

**Platforms.** Packages that carry native libraries are built for one platform, and the
catalog says which (`linux-x86_64-py312`, the platform of SyntH's image).

## The pipeline

`.github/workflows/publish.yml` builds and publishes on every push to `develop` or `main`
(and on `workflow_dispatch` or a `core-updated` repository dispatch). A pull request only
builds, unsigned.

1. It checks out the SyntH repository (`XargonWan/synthetic_heart`): the packer is
   `core/extensions`, the gateway the tests run against is there too, and so are the
   Stage client's sources (`frontend/`). The extensions' sources are `extensions/` here.
2. It runs the extensions' tests in SyntH's environment (each extension's own are in its
   `tests/` folder; `ext_tests/` holds only what spans several extensions):
   `cd $SYNTH_CORE && uv run pytest -c <this repo>/pytest.ini --rootdir <this repo> <this repo>`.
3. `scripts/build_release.sh` builds the Stage client, packs every extension with its
   libraries and writes the signed catalog.
4. `scripts/verify_release.py` refuses to go on unless the catalog verifies with this
   repository's `public.key` and every file matches it.
5. It moves the rolling tag to the commit, creates or updates the release, uploads the
   assets (replacing the old ones, deleting those no longer built).

**Set up once** (repository settings):

| What | Value |
| --- | --- |
| Secret `SYNTH_PUBLISHER_KEY` | the publisher key file, base64: `base64 -w0 ~/.config/synth/publisher.key` |
| Variable `SYNTH_CORE_REF` (optional) | the SyntH ref to build from, for example `refactor/plugin-system` until it is merged; default `develop` / `main` |

The publisher key is the one secret here: keep it out of every repository and back it up. A
lost or leaked key means every installation must be told about a new one.

Locally: `SYNTH_CORE=~/gits/Synthetic_Heart scripts/build_release.sh` writes `dist/`.

## Writing an extension

The rules an extension (and an agent writing one) must keep are in [AGENTS.md](AGENTS.md).

### Screenshots

An extension can ship a `screenshots/` folder in its own directory (`.png`, `.jpg`, `.webp` or
`.gif`, at most 8, each at most 2 MB). The catalog shows them in file-name order (`01-main.png`,
`02-settings.png`, ...), before the extension is installed. The release carries each one as an
asset next to the package; the signed catalog lists them with their SHA-256.

### Start from the template

The `template` branch of this repository is a complete, empty repository for **your own**
extensions: a commented sample extension (`extensions/hello/`), tests, scripts to test and
install locally, a publishing workflow and its own README with the full instructions. It has
no shared history with this branch, so clone it as the `main` of a new repository:

```bash
git clone --branch template --single-branch \
    https://github.com/XargonWan/synthetic-heart-official-plugins.git my-synth-extensions
cd my-synth-extensions
rm -rf .git && git init -b main && git add . && git commit -m "chore: start from the SyntH extension template"
git remote add origin git@github.com:YOU/my-synth-extensions.git && git push -u origin main
```

To contribute to **this** repository instead, branch from `develop` and add your folder to
`extensions/` (and to `EXTENSIONS` in `scripts/build_release.sh`).

### Ask a coding agent to write one

Open your agent in the repository (the template's or this one) and give it a prompt like
this, filling in the paragraph about what the extension does:

> Read `AGENTS.md` and the SyntH platform documentation in `<SyntH checkout>/docs/plugin_platform/`
> (`extension_developer_guide.rst`, `extension_runtime.rst`) and the SDK in
> `<SyntH checkout>/synth_sdk/runtime.py`. Then create a new SyntH extension
> `yourname/<name>` in `extensions/<name>/`, starting from `extensions/hello/` (or, here,
> the official extension closest to it).
>
> **What it should do:** <what service or device it connects to, what a person sees in SyntH,
> which settings it needs (and which are secret), which messages or events it sends or receives>.
>
> **Rules:** follow `AGENTS.md` to the letter. Never import `core`, `plugins`, `interface` or
> `engines`; use only `synth_sdk` and the libraries you bundle in `[python] requirements`.
> Declare only the capabilities you really use. If the API cannot do what is needed, stop and
> tell me which API is missing instead of working around it.
>
> **Deliver:** the manifest (id, version `0.1.0`, `experimental = true`, `[config.*]` for each
> setting, `sensitive = true` for secrets), `ext_main.py`, the pure parts in `convert.py`, a
> `guide.md` for the person installing it, an `icon.svg`, and tests in its own `tests/` folder: a real
> extension process behind the real gateway and a **fake of the outside service**. Run the
> tests until they are green, prove at least one test can fail by breaking the code, and build
> the package. Report what you could not verify.

### Test it locally, without publishing

You need a SyntH checkout (`SYNTH_CORE`, default `../Synthetic_Heart`, after `uv sync` in it).

1. **Tests.** They run a real extension process behind the Core's real gateway:

   ```bash
   cd $SYNTH_CORE && DB_HOST=127.0.0.1 DB_PORT=1 uv run pytest -c <this repo>/pytest.ini \
       --rootdir <this repo> <this repo> -k myextension
   ```

2. **A local repository.** `scripts/build_release.sh` writes `dist/` (one zip per extension and
   the catalog; unsigned unless you have a publisher key). A directory is a repository:

   ```bash
   SYNTH_CORE=~/gits/Synthetic_Heart scripts/build_release.sh
   cd $SYNTH_CORE
   SYNTH_EXT_ROOT=/tmp/try uv run python -m core.extensions.cli repo add mine <this repo>/dist --kind local --unsigned --trusted
   SYNTH_EXT_ROOT=/tmp/try uv run python -m core.extensions.cli install yourname/myextension --grant
   ```

   `SYNTH_EXT_ROOT` is a throwaway runtime root, so a running SyntH is untouched.
3. **In a running SyntH.** Add the same folder in **Plugins > Repositories** (path as location,
   public key empty, **Trusted** ticked). In Docker the path must exist in the container: mount
   `dist/` with a `docker-compose.override.yml` (`volumes: - /abs/path/dist:/local-repo:ro` on
   the `synth` service) and use `/local-repo`. Then **Catalog > Install**, grant what the
   manifest declares, and read its log lines in **Logs**.

   Edit loop: change the code, **bump `version`** (an installed version is never replaced by
   the same number), rebuild, **Update**; **Roll back** returns to the previous version.

The template's README walks through all of this, including publishing your own signed catalog.
